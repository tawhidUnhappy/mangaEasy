//! Job runner — spawn `uv tool run mangaeasy <command>`, stream output to
//! the frontend as `terminal:output` events, report status via `job:start`
//! and `job:finish`.

use std::path::PathBuf;
use std::process::Stdio;
use tauri::{AppHandle, Emitter, Manager};
use tokio::io::AsyncReadExt;
use tokio::sync::mpsc;

use crate::state::{AppState, JobInfo};

// ---------------------------------------------------------------------------
// uv binary resolution
// ---------------------------------------------------------------------------

/// Return the path to the bundled uv sidecar, falling back to system uv.
pub fn uv_path(app: &AppHandle) -> PathBuf {
    if let Ok(res) = app.path().resource_dir() {
        let name = if cfg!(windows) { "uv.exe" } else { "uv" };
        let p = res.join(name);
        if p.exists() {
            return p;
        }
    }
    PathBuf::from(if cfg!(windows) { "uv.exe" } else { "uv" })
}

// ---------------------------------------------------------------------------
// Emit helpers
// ---------------------------------------------------------------------------

fn emit_output(app: &AppHandle, text: &str) {
    // Store in history
    {
        let state = app.state::<AppState>();
        state.history.lock().unwrap().push(text);
    }
    let _ = app.emit("terminal:output", text);
}

fn log_line(app: &AppHandle, text: &str) {
    let line = format!("{}\r\n", text);
    emit_output(app, &line);
}

// ---------------------------------------------------------------------------
// run_job — start a mangaeasy command, stream output
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn run_job(
    app: AppHandle,
    command: String,
    args: Vec<String>,
) -> Result<(), String> {
    // Guard: only one job at a time
    {
        let state = app.state::<AppState>();
        let job = state.job.lock().unwrap();
        if job.is_some() {
            return Err("Another job is already running".into());
        }
    }

    let root = {
        let state = app.state::<AppState>();
        let project_root = state.project_root.lock().unwrap();
        project_root.clone()
    };

    let uv = uv_path(&app);

    // Build: uv tool run mangaeasy <command> [args...]
    let mut cmd = tokio::process::Command::new(&uv);
    cmd.args(["tool", "run", "mangaeasy", &command]);
    cmd.args(&args);
    cmd.current_dir(&root)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("MANGAEASY_PROJECT_ROOT", root.to_string_lossy().as_ref())
        .env("MANGAEASY_APP_MODE", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null());

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to start '{}': {}", command, e))?;

    let pid = child.id();

    // Announce
    let header = format!(
        "\x1b[2m{}\x1b[0m\r\n\x1b[1;36m$ mangaeasy {} {}\x1b[0m\r\n",
        "─".repeat(60),
        command,
        args.join(" ")
    );
    emit_output(&app, &header);
    let _ = app.emit("job:start", &command);

    // Store job info
    {
        let state = app.state::<AppState>();
        *state.job.lock().unwrap() = Some(JobInfo { name: command.clone(), pid });
    }

    // Spawn a background task to drain stdout + stderr
    let app2 = app.clone();
    let cmd2 = command.clone();

    tokio::spawn(async move {
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();

        let (tx, mut rx) = mpsc::channel::<Vec<u8>>(64);

        // Drain stdout
        if let Some(mut out) = stdout {
            let tx2 = tx.clone();
            tokio::spawn(async move {
                let mut buf = vec![0u8; 512];
                loop {
                    match out.read(&mut buf).await {
                        Ok(0) | Err(_) => break,
                        Ok(n) => { tx2.send(buf[..n].to_vec()).await.ok(); }
                    }
                }
            });
        }
        // Drain stderr
        if let Some(mut err) = stderr {
            let tx3 = tx.clone();
            tokio::spawn(async move {
                let mut buf = vec![0u8; 512];
                loop {
                    match err.read(&mut buf).await {
                        Ok(0) | Err(_) => break,
                        Ok(n) => { tx3.send(buf[..n].to_vec()).await.ok(); }
                    }
                }
            });
        }
        drop(tx);

        while let Some(chunk) = rx.recv().await {
            let text = String::from_utf8_lossy(&chunk).into_owned();
            emit_output(&app2, &text);
        }

        let code = child.wait().await
            .map(|s| s.code().unwrap_or(-1))
            .unwrap_or(-1);

        let col = if code == 0 { "\x1b[32m" } else { "\x1b[31m" };
        log_line(&app2, &format!("{col}[{cmd2}] exit {code}\x1b[0m"));

        // Clear job, emit finish
        {
            let state = app2.state::<AppState>();
            *state.job.lock().unwrap() = None;
        }
        let _ = app2.emit("job:finish", code);
    });

    Ok(())
}

// ---------------------------------------------------------------------------
// stop_job — kill the running child process by PID
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn stop_job(app: AppHandle) -> Result<(), String> {
    let pid = {
        let state = app.state::<AppState>();
        let job = state.job.lock().unwrap();
        job.as_ref().and_then(|j| j.pid)
    };
    if let Some(pid) = pid {
        kill_pid(pid);
        log_line(&app, "\x1b[33m[app] stop requested\x1b[0m");
    }
    Ok(())
}

fn kill_pid(pid: u32) {
    #[cfg(windows)]
    { let _ = std::process::Command::new("taskkill").args(["/F", "/PID", &pid.to_string()]).spawn(); }
    #[cfg(not(windows))]
    { let _ = std::process::Command::new("kill").args(["-TERM", &pid.to_string()]).spawn(); }
}

// ---------------------------------------------------------------------------
// is_job_running
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn is_job_running(app: AppHandle) -> bool {
    app.state::<AppState>().job.lock().unwrap().is_some()
}

#[tauri::command]
pub fn job_status(app: AppHandle) -> Option<JobInfo> {
    app.state::<AppState>().job.lock().unwrap().clone()
}

// ---------------------------------------------------------------------------
// get_terminal_history — replay to a newly connected frontend
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn get_terminal_history(app: AppHandle) -> String {
    app.state::<AppState>().history.lock().unwrap().snapshot()
}

// ---------------------------------------------------------------------------
// bootstrap — check & install mangaeasy via bundled uv
// ---------------------------------------------------------------------------

#[derive(serde::Serialize)]
pub struct BootstrapStatus {
    pub uv_found: bool,
    pub uv_path: String,
    pub mangaeasy_installed: bool,
}

#[tauri::command]
pub async fn bootstrap_check(app: AppHandle) -> BootstrapStatus {
    let uv = uv_path(&app);
    let uv_found = uv.exists() || {
        // also try PATH
        std::process::Command::new(if cfg!(windows) { "uv.exe" } else { "uv" })
            .arg("--version")
            .output()
            .is_ok()
    };

    let installed = if uv_found {
        tokio::process::Command::new(&uv)
            .args(["tool", "list"])
            .output()
            .await
            .map(|o| String::from_utf8_lossy(&o.stdout).contains("mangaeasy"))
            .unwrap_or(false)
    } else {
        false
    };

    BootstrapStatus {
        uv_found,
        uv_path: uv.to_string_lossy().into_owned(),
        mangaeasy_installed: installed,
    }
}

#[tauri::command]
pub async fn bootstrap_install(app: AppHandle) -> Result<(), String> {
    let uv = uv_path(&app);
    log_line(&app, "\x1b[1;36m[bootstrap] Installing mangaeasy via uv…\x1b[0m");
    let _ = app.emit("job:start", "bootstrap");

    let state = app.state::<AppState>();
    *state.job.lock().unwrap() = Some(JobInfo {
        name: "bootstrap".into(),
        pid: None,
    });

    let app2 = app.clone();
    tokio::spawn(async move {
        let mut cmd = tokio::process::Command::new(&uv);
        cmd.args(["tool", "install", "mangaeasy"])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(Stdio::null());

        match cmd.spawn() {
            Err(e) => {
                log_line(&app2, &format!("\x1b[31m[bootstrap] Failed: {}\x1b[0m", e));
                let _ = app2.emit("job:finish", -1i32);
            }
            Ok(mut child) => {
                let (tx, mut rx) = mpsc::channel::<Vec<u8>>(64);
                if let Some(mut out) = child.stdout.take() {
                    let tx2 = tx.clone();
                    tokio::spawn(async move {
                        let mut buf = vec![0u8; 256];
                        loop {
                            match out.read(&mut buf).await {
                                Ok(0) | Err(_) => break,
                                Ok(n) => { tx2.send(buf[..n].to_vec()).await.ok(); }
                            }
                        }
                    });
                }
                if let Some(mut err) = child.stderr.take() {
                    let tx3 = tx.clone();
                    tokio::spawn(async move {
                        let mut buf = vec![0u8; 256];
                        loop {
                            match err.read(&mut buf).await {
                                Ok(0) | Err(_) => break,
                                Ok(n) => { tx3.send(buf[..n].to_vec()).await.ok(); }
                            }
                        }
                    });
                }
                drop(tx);
                while let Some(chunk) = rx.recv().await {
                    emit_output(&app2, &String::from_utf8_lossy(&chunk));
                }
                let code = child.wait().await
                    .map(|s| s.code().unwrap_or(-1))
                    .unwrap_or(-1);
                if code == 0 {
                    log_line(&app2, "\x1b[32m[bootstrap] mangaeasy installed ✓\x1b[0m");
                } else {
                    log_line(&app2, &format!("\x1b[31m[bootstrap] install failed (exit {code})\x1b[0m"));
                }
                {
                    let st = app2.state::<AppState>();
                    *st.job.lock().unwrap() = None;
                }
                let _ = app2.emit("job:finish", code);
            }
        }
    });

    Ok(())
}

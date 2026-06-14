//! Config — read/write config.json and config.system.json inside the project root.

use std::path::PathBuf;
use tauri::{AppHandle, Manager};

use crate::state::{save_root, AppState};

// ---------------------------------------------------------------------------
// Project root
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn get_project_root(app: AppHandle) -> String {
    app.state::<AppState>()
        .project_root
        .lock()
        .unwrap()
        .to_string_lossy()
        .into_owned()
}

#[tauri::command]
pub fn set_project_root(app: AppHandle, path: String) -> Result<(), String> {
    let p = PathBuf::from(&path);
    if !p.is_dir() {
        return Err(format!("Not a directory: {}", path));
    }
    let resolved = p.canonicalize().map_err(|e| e.to_string())?;
    let state = app.state::<AppState>();
    *state.project_root.lock().unwrap() = resolved.clone();
    save_root(&resolved);
    Ok(())
}

// ---------------------------------------------------------------------------
// Config files
// ---------------------------------------------------------------------------

fn read_json(path: &std::path::Path) -> serde_json::Value {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(serde_json::Value::Object(Default::default()))
}

#[tauri::command]
pub fn read_config(app: AppHandle) -> (serde_json::Value, serde_json::Value) {
    let root = app.state::<AppState>().project_root.lock().unwrap().clone();
    let cfg    = read_json(&root.join("config.json"));
    let sys    = read_json(&root.join("config.system.json"));
    (cfg, sys)
}

#[tauri::command]
pub fn write_config(
    app: AppHandle,
    cfg:     Option<serde_json::Value>,
    sys_cfg: Option<serde_json::Value>,
) -> Result<(), String> {
    let root = app.state::<AppState>().project_root.lock().unwrap().clone();
    if let Some(v) = cfg {
        let text = serde_json::to_string_pretty(&v).map_err(|e| e.to_string())?;
        std::fs::write(root.join("config.json"), text + "\n")
            .map_err(|e| e.to_string())?;
    }
    if let Some(v) = sys_cfg {
        let text = serde_json::to_string_pretty(&v).map_err(|e| e.to_string())?;
        std::fs::write(root.join("config.system.json"), text + "\n")
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Chapter / workflow status
// ---------------------------------------------------------------------------

#[derive(serde::Serialize)]
pub struct ChapterStatus {
    pub downloads: usize,
    pub panels:    usize,
    pub narration: bool,
    pub narr_items: usize,
    pub audio:     usize,
    pub video:     bool,
    pub ch_dir:    String,
}

fn count_exts(dir: &std::path::Path, exts: &[&str]) -> usize {
    if !dir.is_dir() {
        return 0;
    }
    std::fs::read_dir(dir)
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter(|e| {
                    e.path()
                        .extension()
                        .and_then(|x| x.to_str())
                        .map(|x| exts.iter().any(|&ext| ext.eq_ignore_ascii_case(x)))
                        .unwrap_or(false)
                })
                .count()
        })
        .unwrap_or(0)
}

const IMG: &[&str] = &["png", "jpg", "jpeg", "webp", "gif"];
const AUD: &[&str] = &["wav", "mp3", "m4a"];

fn library_dir(root: &std::path::Path, sys: &serde_json::Value) -> PathBuf {
    if let Some(sub) = sys["paths"]["library_subdir"].as_str() {
        return root.join(sub);
    }
    for candidate in ["mangas", "library", "manga"] {
        let p = root.join(candidate);
        if p.is_dir() {
            return p;
        }
    }
    root.join("mangas")
}

#[tauri::command]
pub fn chapter_status(app: AppHandle, name: String, chapter: u32) -> ChapterStatus {
    let root = app.state::<AppState>().project_root.lock().unwrap().clone();
    let (_, sys) = read_config(app);
    let lib  = library_dir(&root, &sys);
    let ch   = lib.join(&name).join(format!("{:02}", chapter));
    let narr = ch.join(format!("narration_{:02}.json", chapter));

    let narr_items = narr
        .exists()
        .then(|| {
            std::fs::read_to_string(&narr)
                .ok()
                .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
                .and_then(|v| v.as_array().map(|a| a.len()))
                .unwrap_or(0)
        })
        .unwrap_or(0);

    let vid_a = ch.join(format!("{:02}_{}.mp4", chapter, name));
    let vid_b = ch.join(format!("{:02}_{}_with_bgm.mp4", chapter, name));

    ChapterStatus {
        downloads:  count_exts(&ch.join("download"), IMG),
        panels:     count_exts(&ch.join("panels"),   IMG),
        narration:  narr.exists(),
        narr_items,
        audio:      count_exts(&ch.join("audio"),    AUD),
        video:      vid_a.exists() || vid_b.exists(),
        ch_dir:     ch.to_string_lossy().into_owned(),
    }
}

// ---------------------------------------------------------------------------
// File/folder pickers (async — blocks in threadpool via tauri-plugin-dialog)
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn pick_directory(app: AppHandle) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    app.dialog()
        .file()
        .set_title("Select project folder")
        .blocking_pick_folder()
        .map(|p| p.to_string())
}

#[tauri::command]
pub async fn pick_file(app: AppHandle, extensions: Vec<String>) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    let mut builder = app.dialog().file().set_title("Select file");
    for ext in &extensions {
        builder = builder.add_filter("Media", &[ext.as_str()]);
    }
    builder.blocking_pick_file().map(|p| p.to_string())
}

#[tauri::command]
pub async fn open_directory(app: AppHandle, path: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    app.opener()
        .reveal_item_in_dir(std::path::Path::new(&path))
        .map_err(|e| e.to_string())
}

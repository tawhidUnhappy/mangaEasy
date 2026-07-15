from PIL import Image, ImageDraw

size = 512
img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

draw.rounded_rectangle([0, 0, size-1, size-1], radius=90, fill=(18, 18, 38))

px = size // 8
py = size // 8
thick = size // 10
mid_y = size // 2 + thick // 2

pts = [
    (px,           size - py),
    (px,           py),
    (size // 2,    mid_y),
    (size - px,    py),
    (size - px,    size - py),
    (size - px - thick, size - py),
    (size - px - thick, py + thick * 2),
    (size // 2,    mid_y + thick),
    (px + thick,   py + thick * 2),
    (px + thick,   size - py),
]
draw.polygon(pts, fill=(255, 255, 255))

bar_h = thick // 2
draw.rounded_rectangle(
    [px + thick, size - py - bar_h, size - px - thick, size - py + bar_h // 2],
    radius=bar_h // 2,
    fill=(255, 80, 80),
)

img.save('packaging/icon.png')

ico_sizes = [(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]
img.save('packaging/icon.ico', format='ICO', sizes=ico_sizes)

print("Done: packaging/icon.png  packaging/icon.ico")

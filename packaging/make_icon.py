"""Draw the app icon (a red tile with white text lines) as a macOS .iconset.

Usage: python make_icon.py <output.iconset>   (macOS only, needs pyobjc)
"""

import subprocess
import sys
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSColor,
    NSDeviceRGBColorSpace,
    NSGraphicsContext,
    NSMakeRect,
)

SIZE = 1024


def draw(path: Path) -> None:
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, SIZE, SIZE, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.setCurrentContext_(context)

    NSColor.colorWithSRGBRed_green_blue_alpha_(0.77, 0.19, 0.17, 1.0).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(100, 100, 824, 824), 185, 185).fill()

    NSColor.whiteColor().setFill()
    for y, width in ((652, 520), (536, 520), (420, 520), (304, 320)):
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(252, y, width, 68), 34, 34).fill()

    context.flushGraphics()
    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    if data is None or not data.writeToFile_atomically_(str(path), True):
        sys.exit(f"Could not draw the icon to {path}")


def main() -> None:
    iconset = Path(sys.argv[1])
    iconset.mkdir(parents=True, exist_ok=True)
    master = iconset / "icon_512x512@2x.png"
    draw(master)
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
            if iconset / name == master:
                continue
            pixels = str(points * scale)
            subprocess.run(["sips", "-z", pixels, pixels, str(master), "--out", str(iconset / name)],
                           check=True, capture_output=True)


if __name__ == "__main__":
    main()

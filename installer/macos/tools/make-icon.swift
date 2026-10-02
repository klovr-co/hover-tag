// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Draws the Tag artwork onto a macOS-style rounded-square app icon plate:
// 824 px content on a 1024 px canvas, matching Apple's icon grid.
import AppKit

let arguments = CommandLine.arguments
guard arguments.count == 3, let art = NSImage(contentsOfFile: arguments[1]) else {
    FileHandle.standardError.write("usage: make-icon <artwork.png> <out.png>\n".data(using: .utf8)!)
    exit(2)
}
let canvas = 1024, inset = 100, radius: CGFloat = 185
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: canvas, pixelsHigh: canvas, bitsPerSample: 8,
                              samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                              bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
let plate = NSRect(x: inset, y: inset, width: canvas - 2 * inset, height: canvas - 2 * inset)
let shape = NSBezierPath(roundedRect: plate, xRadius: radius, yRadius: radius)
let shadow = NSShadow()
shadow.shadowColor = NSColor.black.withAlphaComponent(0.3)
shadow.shadowOffset = NSSize(width: 0, height: -10)
shadow.shadowBlurRadius = 20
NSGraphicsContext.saveGraphicsState()
shadow.set()
NSColor.white.setFill()
shape.fill()
NSGraphicsContext.restoreGraphicsState()
shape.addClip()
NSGraphicsContext.current?.imageInterpolation = .none  // keep pixel art crisp
art.draw(in: plate)
NSGraphicsContext.restoreGraphicsState()
try! bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: arguments[2]))

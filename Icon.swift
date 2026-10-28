import AppKit
let directory = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
for (name,size) in [("icon_16x16",16),("icon_16x16@2x",32),("icon_32x32",32),("icon_32x32@2x",64),("icon_128x128",128),("icon_128x128@2x",256),("icon_256x256",256),("icon_256x256@2x",512),("icon_512x512",512),("icon_512x512@2x",1024)] {
    let n=CGFloat(size)
    let bitmap=NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:size,pixelsHigh:size,bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current=NSGraphicsContext(bitmapImageRep:bitmap)
    NSColor(calibratedRed:0.08,green:0.36,blue:0.90,alpha:1).setFill()
    NSBezierPath(roundedRect:NSRect(x:n*0.06,y:n*0.06,width:n*0.88,height:n*0.88),xRadius:n*0.20,yRadius:n*0.20).fill()
    // Original geometric CPU mark; no third-party logo or system-symbol asset.
    NSColor.white.setStroke()
    let core=NSBezierPath(roundedRect:NSRect(x:n*0.31,y:n*0.31,width:n*0.38,height:n*0.38),xRadius:n*0.05,yRadius:n*0.05)
    core.lineWidth=max(1,n*0.035);core.stroke()
    for offset in [0.39,0.50,0.61] {
        let pins=NSBezierPath();pins.lineWidth=max(1,n*0.025)
        pins.move(to:NSPoint(x:n*offset,y:n*0.21));pins.line(to:NSPoint(x:n*offset,y:n*0.31))
        pins.move(to:NSPoint(x:n*offset,y:n*0.69));pins.line(to:NSPoint(x:n*offset,y:n*0.79))
        pins.move(to:NSPoint(x:n*0.21,y:n*offset));pins.line(to:NSPoint(x:n*0.31,y:n*offset))
        pins.move(to:NSPoint(x:n*0.69,y:n*offset));pins.line(to:NSPoint(x:n*0.79,y:n*offset));pins.stroke()
    }
    NSGraphicsContext.restoreGraphicsState()
    try bitmap.representation(using:.png,properties:[:])!.write(to:directory.appendingPathComponent(name+".png"))
}

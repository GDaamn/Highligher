import AppKit
import AVFoundation
import Foundation

guard CommandLine.arguments.count == 3 else {
    fputs("usage: extract_video_frames <video> <output-directory>\n", stderr)
    exit(2)
}

let videoURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
try FileManager.default.createDirectory(at: outputURL, withIntermediateDirectories: true)

let asset = AVURLAsset(url: videoURL)
let duration = CMTimeGetSeconds(asset.duration)
guard duration.isFinite, duration > 0 else {
    fputs("could not read video duration\n", stderr)
    exit(3)
}

let generator = AVAssetImageGenerator(asset: asset)
generator.appliesPreferredTrackTransform = true
generator.requestedTimeToleranceBefore = .zero
generator.requestedTimeToleranceAfter = .zero

let fractions: [Double] = [0.02, 0.16, 0.30, 0.44, 0.58, 0.72, 0.86, 0.98]
for (index, fraction) in fractions.enumerated() {
    let time = CMTime(seconds: duration * fraction, preferredTimescale: 600)
    let image = try generator.copyCGImage(at: time, actualTime: nil)
    let bitmap = NSBitmapImageRep(cgImage: image)
    guard let data = bitmap.representation(using: .png, properties: [:]) else { continue }
    let destination = outputURL.appendingPathComponent(String(format: "frame-%02d.png", index + 1))
    try data.write(to: destination)
}

print(String(format: "duration=%.3f frames=%d", duration, fractions.count))

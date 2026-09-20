#!/usr/bin/swift
import AppKit
import Foundation
import Vision

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path) else { continue }
    var rect = CGRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { continue }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
        for observation in request.results ?? [] {
            if let candidate = observation.topCandidates(1).first {
                print(candidate.string)
            }
        }
    } catch {
        fputs("Vision OCR failed for \(path): \(error)\n", stderr)
    }
}

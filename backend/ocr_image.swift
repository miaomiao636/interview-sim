import Foundation
import ImageIO
import Vision

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: ocr_image.swift <image-path>\n".utf8))
    exit(2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
guard
    let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
    let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
else {
    FileHandle.standardError.write(Data("无法读取图片\n".utf8))
    exit(3)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["zh-Hans", "en-US"]

do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    let text = (request.results ?? []).compactMap { observation in
        observation.topCandidates(1).first?.string
    }.joined(separator: "\n")
    print(text)
} catch {
    FileHandle.standardError.write(Data("文字识别失败：\(error.localizedDescription)\n".utf8))
    exit(4)
}

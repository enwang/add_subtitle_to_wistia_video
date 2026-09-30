#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        for (int index = 1; index < argc; index++) {
            NSString *path = [NSString stringWithUTF8String:argv[index]];
            NSImage *image = [[NSImage alloc] initWithContentsOfFile:path];
            if (!image) {
                continue;
            }
            CGImageRef cgImage = [image CGImageForProposedRect:NULL context:nil hints:nil];
            if (!cgImage) {
                continue;
            }

            VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
            request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
            request.recognitionLanguages = @[@"zh-Hans", @"zh-Hant", @"en-US"];
            request.usesLanguageCorrection = YES;

            VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:cgImage options:@{}];
            NSError *error = nil;
            if (![handler performRequests:@[request] error:&error]) {
                fprintf(stderr, "Vision OCR failed for %s: %s\n", argv[index], error.localizedDescription.UTF8String);
                continue;
            }
            for (VNRecognizedTextObservation *observation in request.results) {
                VNRecognizedText *candidate = [[observation topCandidates:1] firstObject];
                if (candidate) {
                    printf("%s\n", candidate.string.UTF8String);
                }
            }
        }
    }
    return 0;
}

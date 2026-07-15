#import <Cocoa/Cocoa.h>
#import <ApplicationServices/ApplicationServices.h>
#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
#import <math.h>

static NSString *const QSServerBase = @"http://127.0.0.1:8990";
static const CGKeyCode QSRightCommandKeyCode = 54;
static const CGKeyCode QSPasteKeyCode = 9;

static void QSAppendString(NSMutableData *data, NSString *string) {
    [data appendData:[string dataUsingEncoding:NSUTF8StringEncoding]];
}

typedef NS_ENUM(NSInteger, QSHUDState) {
    QSHUDStateListening,
    QSHUDStateTranscribing,
    QSHUDStateInserted,
    QSHUDStateError,
};

@interface QSHUDView : NSView
@property (nonatomic) QSHUDState state;
@property (nonatomic) CGFloat phase;
@property (nonatomic, strong) NSTimer *animationTimer;
- (void)showState:(QSHUDState)state;
- (void)stopAnimating;
@end

@implementation QSHUDView

- (BOOL)isFlipped { return YES; }

- (void)showState:(QSHUDState)state {
    self.state = state;
    self.phase = 0;
    [self.animationTimer invalidate];
    self.animationTimer = nil;
    if (state == QSHUDStateListening || state == QSHUDStateTranscribing) {
        __weak typeof(self) weakSelf = self;
        self.animationTimer = [NSTimer scheduledTimerWithTimeInterval:0.075 repeats:YES block:^(NSTimer *timer) {
            weakSelf.phase += 0.34;
            weakSelf.needsDisplay = YES;
        }];
    }
    self.needsDisplay = YES;
}

- (void)stopAnimating {
    [self.animationTimer invalidate];
    self.animationTimer = nil;
}

- (void)drawRect:(NSRect)dirtyRect {
    NSRect bounds = NSInsetRect(self.bounds, 1, 1);
    NSBezierPath *background = [NSBezierPath bezierPathWithRoundedRect:bounds xRadius:14 yRadius:14];
    [[NSColor colorWithRed:0.075 green:0.071 blue:0.105 alpha:0.96] setFill];
    [background fill];
    [[NSColor colorWithWhite:1 alpha:0.14] setStroke];
    background.lineWidth = 1;
    [background stroke];

    NSColor *accent;
    NSString *label;
    switch (self.state) {
        case QSHUDStateListening:
            accent = [NSColor colorWithRed:0.96 green:0.25 blue:0.35 alpha:1];
            label = @"Listening…";
            break;
        case QSHUDStateTranscribing:
            accent = [NSColor colorWithRed:0.98 green:0.63 blue:0.25 alpha:1];
            label = @"Transcribing…";
            break;
        case QSHUDStateInserted:
            accent = [NSColor colorWithRed:0.44 green:0.82 blue:0.59 alpha:1];
            label = @"Text inserted";
            break;
        default:
            accent = [NSColor colorWithRed:0.90 green:0.39 blue:0.44 alpha:1];
            label = @"Dictation failed";
            break;
    }

    CGFloat centerY = NSMidY(self.bounds);
    CGFloat textX = 44;
    if (self.state == QSHUDStateListening || self.state == QSHUDStateTranscribing) {
        CGFloat pulse = 0.5 + 0.5 * sin(self.phase);
        [[accent colorWithAlphaComponent:0.15 + pulse * 0.12] setFill];
        [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(14 - pulse * 2, centerY - 7 - pulse * 2,
                                                           14 + pulse * 4, 14 + pulse * 4)] fill];
        [accent setFill];
        [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(18, centerY - 3, 6, 6)] fill];

        for (NSInteger i = 0; i < 5; i++) {
            CGFloat energy = fabs(sin(self.phase + i * 0.86));
            CGFloat height = 6 + energy * (self.state == QSHUDStateListening ? 15 : 9);
            NSRect barRect = NSMakeRect(34 + i * 6, centerY - height / 2, 3, height);
            NSBezierPath *bar = [NSBezierPath bezierPathWithRoundedRect:barRect xRadius:1.5 yRadius:1.5];
            [[accent colorWithAlphaComponent:0.65 + energy * 0.35] setFill];
            [bar fill];
        }
        textX = 74;
    } else if (self.state == QSHUDStateInserted) {
        NSBezierPath *check = [NSBezierPath bezierPath];
        [check moveToPoint:NSMakePoint(16, centerY)];
        [check lineToPoint:NSMakePoint(21, centerY + 5)];
        [check lineToPoint:NSMakePoint(29, centerY - 5)];
        check.lineWidth = 2.4;
        check.lineCapStyle = NSLineCapStyleRound;
        check.lineJoinStyle = NSLineJoinStyleRound;
        [accent setStroke];
        [check stroke];
    } else {
        [accent setFill];
        [[NSBezierPath bezierPathWithOvalInRect:NSMakeRect(17, centerY - 4, 8, 8)] fill];
    }

    NSDictionary *attributes = @{
        NSFontAttributeName: [NSFont systemFontOfSize:13.5 weight:NSFontWeightSemibold],
        NSForegroundColorAttributeName: [NSColor colorWithWhite:0.96 alpha:1],
    };
    NSSize textSize = [label sizeWithAttributes:attributes];
    [label drawAtPoint:NSMakePoint(textX, centerY - textSize.height / 2) withAttributes:attributes];
}

@end

@interface QSDictationHUD : NSObject
@property (nonatomic, strong) NSPanel *panel;
@property (nonatomic, strong) QSHUDView *view;
@property (nonatomic) NSInteger generation;
- (void)showState:(QSHUDState)state;
- (void)hide;
@end

@implementation QSDictationHUD

- (instancetype)init {
    self = [super init];
    if (self) {
        NSRect frame = NSMakeRect(0, 0, 196, 50);
        self.panel = [[NSPanel alloc] initWithContentRect:frame
                                                styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
                                                  backing:NSBackingStoreBuffered
                                                    defer:NO];
        self.panel.opaque = NO;
        self.panel.backgroundColor = NSColor.clearColor;
        self.panel.hasShadow = YES;
        self.panel.hidesOnDeactivate = NO;
        self.panel.ignoresMouseEvents = YES;
        self.panel.releasedWhenClosed = NO;
        self.panel.level = NSFloatingWindowLevel;
        self.panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
                                        NSWindowCollectionBehaviorFullScreenAuxiliary |
                                        NSWindowCollectionBehaviorIgnoresCycle;
        self.panel.animationBehavior = NSWindowAnimationBehaviorUtilityWindow;
        self.view = [[QSHUDView alloc] initWithFrame:frame];
        self.panel.contentView = self.view;
    }
    return self;
}

- (void)showState:(QSHUDState)state {
    self.generation += 1;
    [self.view showState:state];
    NSScreen *screen = NSScreen.mainScreen ?: NSScreen.screens.firstObject;
    NSRect visible = screen.visibleFrame;
    NSRect frame = self.panel.frame;
    frame.origin.x = NSMidX(visible) - frame.size.width / 2;
    frame.origin.y = NSMaxY(visible) - frame.size.height - 18;
    [self.panel setFrame:frame display:YES];
    if (!self.panel.visible) {
        self.panel.alphaValue = 0;
        [self.panel orderFrontRegardless];
        [NSAnimationContext runAnimationGroup:^(NSAnimationContext *context) {
            context.duration = 0.12;
            self.panel.animator.alphaValue = 1;
        }];
    } else {
        self.panel.alphaValue = 1;
    }

    if (state == QSHUDStateInserted || state == QSHUDStateError) {
        NSInteger expectedGeneration = self.generation;
        NSTimeInterval delay = state == QSHUDStateInserted ? 0.9 : 1.35;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(delay * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            if (self.generation == expectedGeneration) [self hide];
        });
    }
}

- (void)hide {
    self.generation += 1;
    [self.view stopAnimating];
    [NSAnimationContext runAnimationGroup:^(NSAnimationContext *context) {
        context.duration = 0.12;
        self.panel.animator.alphaValue = 0;
    } completionHandler:^{
        [self.panel orderOut:nil];
    }];
}

@end

@interface QSDictationDelegate : NSObject <NSApplicationDelegate>
@property (nonatomic, strong) id globalMonitor;
@property (nonatomic, strong) id localMonitor;
@property (nonatomic, strong) NSTimer *heartbeatTimer;
@property (nonatomic, strong) AVAudioRecorder *recorder;
@property (nonatomic, strong) NSURL *recordingURL;
@property (nonatomic, strong) NSDate *recordingStartedAt;
@property (nonatomic, strong) NSRunningApplication *targetApplication;
@property (nonatomic, strong) QSDictationHUD *hud;
@property (nonatomic) BOOL rightCommandIsDown;
@property (nonatomic) BOOL busy;
@end

@implementation QSDictationDelegate

- (NSString *)dictationPIDFile {
    NSString *support = [NSHomeDirectory() stringByAppendingPathComponent:
        @"Library/Application Support/Qwen Scribe"];
    [[NSFileManager defaultManager] createDirectoryAtPath:support
                              withIntermediateDirectories:YES
                                               attributes:nil
                                                    error:nil];
    return [support stringByAppendingPathComponent:@"dictation.pid"];
}

- (void)writeProcessIdentity {
    NSString *pid = [NSString stringWithFormat:@"%d\n", NSProcessInfo.processInfo.processIdentifier];
    [pid writeToFile:[self dictationPIDFile]
          atomically:YES
            encoding:NSUTF8StringEncoding
               error:nil];
}

- (void)removeProcessIdentity {
    NSString *path = [self dictationPIDFile];
    NSString *recorded = [NSString stringWithContentsOfFile:path
                                                   encoding:NSUTF8StringEncoding
                                                      error:nil];
    if (recorded.integerValue == NSProcessInfo.processInfo.processIdentifier) {
        [[NSFileManager defaultManager] removeItemAtPath:path error:nil];
    }
}

- (void)launchLocalServer {
    NSString *script = [NSBundle.mainBundle pathForResource:@"launch-server" ofType:@"sh"];
    if (!script) {
        [self reportFailure:@"The local server launcher is missing"];
        return;
    }

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:@"/bin/bash"];
    task.arguments = @[script];
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        [self reportFailure:[NSString stringWithFormat:@"Could not start the local server: %@",
                             error.localizedDescription ?: @"unknown error"]];
    }
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    self.hud = [[QSDictationHUD alloc] init];
    [self writeProcessIdentity];

    NSDictionary *accessibilityOptions = @{
        (__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES
    };
    AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)accessibilityOptions);
    if (!CGPreflightListenEventAccess()) {
        CGRequestListenEventAccess();
    }
    [self requestMicrophoneAccess];

    __weak typeof(self) weakSelf = self;
    self.globalMonitor = [NSEvent addGlobalMonitorForEventsMatchingMask:NSEventMaskFlagsChanged
                                                               handler:^(NSEvent *event) {
        dispatch_async(dispatch_get_main_queue(), ^{ [weakSelf handleFlagsChanged:event]; });
    }];
    self.localMonitor = [NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskFlagsChanged
                                                               handler:^NSEvent *(NSEvent *event) {
        [weakSelf handleFlagsChanged:event];
        return event;
    }];

    [self sendHeartbeat];
    self.heartbeatTimer = [NSTimer scheduledTimerWithTimeInterval:10
                                                          repeats:YES
                                                            block:^(NSTimer *timer) {
        [weakSelf sendHeartbeat];
    }];

    [self launchLocalServer];
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    [self.recorder stop];
    [self.hud hide];
    if (self.globalMonitor) [NSEvent removeMonitor:self.globalMonitor];
    if (self.localMonitor) [NSEvent removeMonitor:self.localMonitor];
    [self.heartbeatTimer invalidate];
    if (self.recordingURL) {
        [[NSFileManager defaultManager] removeItemAtURL:self.recordingURL error:nil];
    }
    [self removeProcessIdentity];
}

- (void)requestMicrophoneAccess {
    if ([AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] == AVAuthorizationStatusNotDetermined) {
        __weak typeof(self) weakSelf = self;
        [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio completionHandler:^(BOOL granted) {
            if (!granted) [weakSelf playSound:@"Basso"];
            [weakSelf sendHeartbeat];
        }];
    }
}

- (void)handleFlagsChanged:(NSEvent *)event {
    if (event.keyCode != QSRightCommandKeyCode) return;
    BOOL isDown = (event.modifierFlags & NSEventModifierFlagCommand) != 0;
    if (isDown == self.rightCommandIsDown) return;
    self.rightCommandIsDown = isDown;
    if (isDown) [self beginRecording];
    else [self finishRecording];
}

- (void)beginRecording {
    if (self.busy) {
        [self playSound:@"Basso"];
        return;
    }
    if ([AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] != AVAuthorizationStatusAuthorized) {
        [self requestMicrophoneAccess];
        [self playSound:@"Basso"];
        return;
    }

    NSString *filename = [NSString stringWithFormat:@"qwen-scribe-dictation-%@.wav", NSUUID.UUID.UUIDString];
    NSURL *url = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:filename]];
    NSDictionary *settings = @{
        AVFormatIDKey: @(kAudioFormatLinearPCM),
        AVSampleRateKey: @16000.0,
        AVNumberOfChannelsKey: @1,
        AVLinearPCMBitDepthKey: @16,
        AVLinearPCMIsFloatKey: @NO,
        AVLinearPCMIsBigEndianKey: @NO,
    };

    NSError *error = nil;
    AVAudioRecorder *recorder = [[AVAudioRecorder alloc] initWithURL:url settings:settings error:&error];
    [recorder prepareToRecord];
    if (error || ![recorder record]) {
        [self reportFailure:[NSString stringWithFormat:@"Could not start recording: %@", error.localizedDescription ?: @"unknown error"]];
        return;
    }

    self.recorder = recorder;
    self.recordingURL = url;
    self.recordingStartedAt = [NSDate date];
    self.targetApplication = NSWorkspace.sharedWorkspace.frontmostApplication;
    self.busy = YES;
    [self.hud showState:QSHUDStateListening];
    [self playSound:@"Tink"];
}

- (void)finishRecording {
    if (!self.recorder || !self.recordingURL) return;
    [self.recorder stop];
    self.recorder = nil;
    NSTimeInterval duration = -[self.recordingStartedAt timeIntervalSinceNow];
    self.recordingStartedAt = nil;
    [self playSound:@"Pop"];

    if (duration < 0.25) {
        [self.hud hide];
        [[NSFileManager defaultManager] removeItemAtURL:self.recordingURL error:nil];
        self.recordingURL = nil;
        self.targetApplication = nil;
        self.busy = NO;
        return;
    }
    [self.hud showState:QSHUDStateTranscribing];
    [self uploadRecording:self.recordingURL];
}

- (void)uploadRecording:(NSURL *)url {
    NSData *audio = [NSData dataWithContentsOfURL:url];
    if (!audio) {
        [self reportFailure:@"Could not read the recording"];
        return;
    }

    NSString *boundary = [NSString stringWithFormat:@"QwenScribe-%@", NSUUID.UUID.UUIDString];
    NSMutableData *body = [NSMutableData data];
    void (^appendField)(NSString *, NSString *) = ^(NSString *name, NSString *value) {
        QSAppendString(body, [NSString stringWithFormat:@"--%@\r\n", boundary]);
        QSAppendString(body, [NSString stringWithFormat:@"Content-Disposition: form-data; name=\"%@\"\r\n\r\n", name]);
        QSAppendString(body, [NSString stringWithFormat:@"%@\r\n", value]);
    };
    appendField(@"model", @"1.7b");
    appendField(@"language", @"auto");
    appendField(@"timestamps", @"false");
    appendField(@"turbo", @"false");
    appendField(@"context", @"");
    QSAppendString(body, [NSString stringWithFormat:@"--%@\r\n", boundary]);
    NSDateFormatter *filenameFormatter = [[NSDateFormatter alloc] init];
    filenameFormatter.locale = [NSLocale localeWithLocaleIdentifier:@"en_US_POSIX"];
    filenameFormatter.dateFormat = @"yyyy-MM-dd HH.mm.ss";
    NSString *uploadName = [NSString stringWithFormat:@"Dictation %@.wav", [filenameFormatter stringFromDate:[NSDate date]]];
    QSAppendString(body, [NSString stringWithFormat:@"Content-Disposition: form-data; name=\"file\"; filename=\"%@\"\r\n", uploadName]);
    QSAppendString(body, @"Content-Type: audio/wav\r\n\r\n");
    [body appendData:audio];
    QSAppendString(body, [NSString stringWithFormat:@"\r\n--%@--\r\n", boundary]);

    NSURL *endpoint = [NSURL URLWithString:[QSServerBase stringByAppendingString:@"/api/jobs"]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:endpoint];
    request.HTTPMethod = @"POST";
    [request setValue:[NSString stringWithFormat:@"multipart/form-data; boundary=%@", boundary]
   forHTTPHeaderField:@"Content-Type"];
    request.HTTPBody = body;

    __weak typeof(self) weakSelf = self;
    [[NSURLSession.sharedSession dataTaskWithRequest:request
                                  completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        NSHTTPURLResponse *http = (NSHTTPURLResponse *)response;
        NSDictionary *json = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        NSString *jobID = json[@"id"];
        if (error || http.statusCode < 200 || http.statusCode >= 300 || !jobID) {
            [weakSelf reportFailure:@"Qwen Scribe server is unavailable"];
            return;
        }
        [weakSelf pollJob:jobID attempt:0];
    }] resume];
}

- (void)pollJob:(NSString *)jobID attempt:(NSInteger)attempt {
    if (attempt >= 1200) {
        [self reportFailure:@"Dictation timed out"];
        return;
    }
    NSURL *url = [NSURL URLWithString:[NSString stringWithFormat:@"%@/api/jobs/%@", QSServerBase, jobID]];
    __weak typeof(self) weakSelf = self;
    [[NSURLSession.sharedSession dataTaskWithURL:url
                              completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        NSDictionary *state = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        if (error || !state) {
            [weakSelf reportFailure:@"Lost connection to Qwen Scribe"];
            return;
        }
        NSString *status = state[@"status"];
        if ([status isEqualToString:@"done"]) {
            NSString *text = state[@"result"][@"text"];
            text = [text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
            if (text.length == 0) {
                [weakSelf reportFailure:@"No speech detected"];
                return;
            }
            dispatch_async(dispatch_get_main_queue(), ^{ [weakSelf pasteText:text]; });
        } else if ([status isEqualToString:@"error"]) {
            [weakSelf reportFailure:state[@"detail"] ?: @"Transcription failed"];
        } else {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)),
                           dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
                [weakSelf pollJob:jobID attempt:attempt + 1];
            });
        }
    }] resume];
}

- (void)pasteText:(NSString *)text {
    [self.targetApplication activateWithOptions:0];
    __weak typeof(self) weakSelf = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.15 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
        NSArray *snapshot = [weakSelf snapshotPasteboard:pasteboard];
        [pasteboard clearContents];
        [pasteboard setString:text forType:NSPasteboardTypeString];
        NSInteger injectedChangeCount = pasteboard.changeCount;

        CGEventSourceRef source = CGEventSourceCreate(kCGEventSourceStateHIDSystemState);
        CGEventRef keyDown = CGEventCreateKeyboardEvent(source, QSPasteKeyCode, true);
        CGEventRef keyUp = CGEventCreateKeyboardEvent(source, QSPasteKeyCode, false);
        CGEventSetFlags(keyDown, kCGEventFlagMaskCommand);
        CGEventSetFlags(keyUp, kCGEventFlagMaskCommand);
        CGEventPost(kCGHIDEventTap, keyDown);
        CGEventPost(kCGHIDEventTap, keyUp);
        CFRelease(keyDown);
        CFRelease(keyUp);
        CFRelease(source);
        [weakSelf playSound:@"Glass"];
        [weakSelf.hud showState:QSHUDStateInserted];

        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.7 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            if (pasteboard.changeCount == injectedChangeCount) {
                [weakSelf restorePasteboard:snapshot to:pasteboard];
            }
        });
        if (weakSelf.recordingURL) {
            [[NSFileManager defaultManager] removeItemAtURL:weakSelf.recordingURL error:nil];
        }
        weakSelf.recordingURL = nil;
        weakSelf.targetApplication = nil;
        weakSelf.busy = NO;
    });
}

- (NSArray<NSDictionary<NSPasteboardType, NSData *> *> *)snapshotPasteboard:(NSPasteboard *)pasteboard {
    NSMutableArray *snapshot = [NSMutableArray array];
    for (NSPasteboardItem *item in pasteboard.pasteboardItems ?: @[]) {
        NSMutableDictionary *values = [NSMutableDictionary dictionary];
        for (NSPasteboardType type in item.types) {
            NSData *data = [item dataForType:type];
            if (data) values[type] = data;
        }
        [snapshot addObject:values];
    }
    return snapshot;
}

- (void)restorePasteboard:(NSArray<NSDictionary<NSPasteboardType, NSData *> *> *)snapshot
                       to:(NSPasteboard *)pasteboard {
    [pasteboard clearContents];
    NSMutableArray *items = [NSMutableArray array];
    for (NSDictionary *values in snapshot) {
        NSPasteboardItem *item = [[NSPasteboardItem alloc] init];
        [values enumerateKeysAndObjectsUsingBlock:^(NSPasteboardType type, NSData *data, BOOL *stop) {
            [item setData:data forType:type];
        }];
        [items addObject:item];
    }
    if (items.count) [pasteboard writeObjects:items];
}

- (void)sendHeartbeat {
    BOOL accessibility = AXIsProcessTrusted();
    BOOL inputMonitoring = CGPreflightListenEventAccess();
    BOOL microphone = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] == AVAuthorizationStatusAuthorized;
    NSString *endpoint = [NSString stringWithFormat:
        @"%@/api/dictation/heartbeat?accessibility=%@&input_monitoring=%@&microphone=%@",
        QSServerBase,
        accessibility ? @"true" : @"false",
        inputMonitoring ? @"true" : @"false",
        microphone ? @"true" : @"false"];
    NSURL *url = [NSURL URLWithString:endpoint];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.HTTPMethod = @"POST";
    [[NSURLSession.sharedSession dataTaskWithRequest:request] resume];
}

- (void)reportFailure:(NSString *)message {
    fprintf(stderr, "Qwen Scribe dictation: %s\n", message.UTF8String);
    __weak typeof(self) weakSelf = self;
    dispatch_async(dispatch_get_main_queue(), ^{
        [weakSelf playSound:@"Basso"];
        [weakSelf.hud showState:QSHUDStateError];
        if (weakSelf.recordingURL) {
            [[NSFileManager defaultManager] removeItemAtURL:weakSelf.recordingURL error:nil];
        }
        weakSelf.recordingURL = nil;
        weakSelf.recorder = nil;
        weakSelf.recordingStartedAt = nil;
        weakSelf.targetApplication = nil;
        weakSelf.busy = NO;
    });
}

- (void)playSound:(NSString *)name {
    dispatch_async(dispatch_get_main_queue(), ^{
        [[NSSound soundNamed:name] play];
    });
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc > 1 && strcmp(argv[1], "--check") == 0) {
            puts("Qwen Scribe desktop dictation helper is installed");
            return 0;
        }
        if (argc > 1 && strcmp(argv[1], "--permissions") == 0) {
            BOOL accessibility = AXIsProcessTrusted();
            BOOL inputMonitoring = CGPreflightListenEventAccess();
            BOOL microphone = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] == AVAuthorizationStatusAuthorized;
            printf("accessibility=%s input_monitoring=%s microphone=%s\n",
                   accessibility ? "granted" : "missing",
                   inputMonitoring ? "granted" : "missing",
                   microphone ? "granted" : "missing");
            return 0;
        }
        if (argc > 2 && strcmp(argv[1], "--render-hud") == 0) {
            NSApplication *application = NSApplication.sharedApplication;
            [application setActivationPolicy:NSApplicationActivationPolicyAccessory];
            NSRect frame = NSMakeRect(0, 0, 196, 50);
            QSHUDView *view = [[QSHUDView alloc] initWithFrame:frame];
            [view showState:QSHUDStateListening];
            [view stopAnimating];
            view.phase = 1.15;
            NSBitmapImageRep *bitmap = [view bitmapImageRepForCachingDisplayInRect:frame];
            [view cacheDisplayInRect:frame toBitmapImageRep:bitmap];
            NSData *png = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
            if (![png writeToFile:[NSString stringWithUTF8String:argv[2]] atomically:YES]) return 1;
            return 0;
        }
        if (argc > 1 && strcmp(argv[1], "--preview-hud") == 0) {
            NSApplication *application = NSApplication.sharedApplication;
            [application setActivationPolicy:NSApplicationActivationPolicyAccessory];
            QSDictationHUD *hud = [[QSDictationHUD alloc] init];
            [hud showState:QSHUDStateListening];
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.8 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
                [hud showState:QSHUDStateTranscribing];
            });
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3.2 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
                [hud showState:QSHUDStateInserted];
            });
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(4.3 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
                [application terminate:nil];
            });
            [application run];
            return 0;
        }
        NSApplication *application = NSApplication.sharedApplication;
        QSDictationDelegate *delegate = [[QSDictationDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}

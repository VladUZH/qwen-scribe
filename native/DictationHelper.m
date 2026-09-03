#import <Cocoa/Cocoa.h>
#import <ApplicationServices/ApplicationServices.h>
#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
#import <math.h>

static NSString *const QSServerBase = @"http://127.0.0.1:8990";
static const CGKeyCode QSPasteKeyCode = 9;

// Push-to-talk keys the user can pick in settings. Ids must match
// DICTATION_HOTKEYS in server.py.
//
// The masks are the device-dependent modifier bits from
// <IOKit/hidsystem/IOLLEvent.h> (NX_DEVICER*KEYMASK): the device-independent
// flags (e.g. NSEventModifierFlagCommand) stay set while the OTHER key of the
// pair is held, which would swallow the key-up and leave the microphone
// recording. Fn is deliberately NOT offered: macOS synthesizes fn-flagged
// keyCode-63 flagsChanged events around every arrow/navigation key, so a
// naive Fn hotkey would start dictation on PageUp. Supporting it needs an
// IOHIDManager listener for the physical key (roadmapped).
typedef struct {
    const char *identifier;
    CGKeyCode keyCode;       // kVK_* virtual key code
    NSEventModifierFlags mask;
    const char *label;
} QSHotkeySpec;

static const QSHotkeySpec QSHotkeyTable[] = {
    {"right_command", 54, 0x00000010, "Right \xE2\x8C\x98"},   // NX_DEVICERCMDKEYMASK
    {"right_option",  61, 0x00000040, "Right \xE2\x8C\xA5"},   // NX_DEVICERALTKEYMASK
    {"right_control", 62, 0x00002000, "Right \xE2\x8C\x83"},   // NX_DEVICERCTLKEYMASK
};

static const QSHotkeySpec *QSHotkeyForIdentifier(NSString *identifier) {
    for (size_t i = 0; i < sizeof(QSHotkeyTable) / sizeof(QSHotkeyTable[0]); i++) {
        if ([identifier isEqualToString:@(QSHotkeyTable[i].identifier)]) {
            return &QSHotkeyTable[i];
        }
    }
    return &QSHotkeyTable[0];   // right Command, the historical default
}

// A held key that is never released (a lost key-up, a Space switch), or a
// toggle nobody ends, must not leave dictation recording forever. The limit
// comes from the settings; these are its default and its bounds, matching
// DICTATION_*_SECONDS in the server's config.
static const NSTimeInterval QSDefaultMaximumRecordingSeconds = 120.0;
static const NSTimeInterval QSMinimumRecordingLimit = 60.0;
static const NSTimeInterval QSMaximumRecordingLimit = 600.0;
// In toggle mode a press shorter than this is a tap; a longer one is a hold.
static const NSTimeInterval QSToggleTapSeconds = 0.4;

static void QSAppendString(NSMutableData *data, NSString *string) {
    [data appendData:[string dataUsingEncoding:NSUTF8StringEncoding]];
}

typedef NS_ENUM(NSInteger, QSHUDState) {
    QSHUDStateListening,
    QSHUDStateTranscribing,
    QSHUDStateLoading,       // the server is loading (or first downloading) the model
    QSHUDStateInserted,
    QSHUDStateError,
};

// Wide enough for "Loading model…" followed by "1.2 of 3.4 GB".
static const CGFloat QSHUDWidth = 244;
static const CGFloat QSHUDHeight = 50;

@interface QSHUDView : NSView
@property (nonatomic) QSHUDState state;
@property (nonatomic) CGFloat phase;
@property (nonatomic, strong) NSTimer *animationTimer;
// Small text after the label: the elapsed time of a toggled recording.
@property (nonatomic, copy) NSString *detail;
- (void)showState:(QSHUDState)state;
- (void)stopAnimating;
@end

@implementation QSHUDView

- (BOOL)isFlipped { return YES; }

- (void)showState:(QSHUDState)state {
    self.state = state;
    self.phase = 0;
    self.detail = nil;
    [self.animationTimer invalidate];
    self.animationTimer = nil;
    if (state == QSHUDStateListening || state == QSHUDStateTranscribing || state == QSHUDStateLoading) {
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
        case QSHUDStateLoading:
            accent = [NSColor colorWithRed:0.62 green:0.47 blue:0.93 alpha:1];
            label = @"Loading model…";
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
    if (self.state == QSHUDStateListening || self.state == QSHUDStateTranscribing || self.state == QSHUDStateLoading) {
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
    if (self.detail.length) {
        NSDictionary *detailAttributes = @{
            NSFontAttributeName: [NSFont monospacedDigitSystemFontOfSize:11 weight:NSFontWeightMedium],
            NSForegroundColorAttributeName: [NSColor colorWithWhite:0.96 alpha:0.7],
        };
        NSSize detailSize = [self.detail sizeWithAttributes:detailAttributes];
        [self.detail drawAtPoint:NSMakePoint(textX + textSize.width + 7, centerY - detailSize.height / 2)
                  withAttributes:detailAttributes];
    }
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
        NSRect frame = NSMakeRect(0, 0, QSHUDWidth, QSHUDHeight);
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

@interface QSDictationDelegate : NSObject <NSApplicationDelegate, NSMenuDelegate>
@property (nonatomic, strong) id globalMonitor;
@property (nonatomic, strong) id localMonitor;
@property (nonatomic, strong) NSTimer *heartbeatTimer;
@property (nonatomic, strong) NSTimer *recordingWatchdog;
@property (nonatomic, strong) dispatch_source_t terminationSource;
@property (nonatomic, strong) AVAudioRecorder *recorder;
@property (nonatomic, strong) NSURL *recordingURL;
@property (nonatomic, strong) NSDate *recordingStartedAt;
@property (nonatomic, strong) NSRunningApplication *targetApplication;
@property (nonatomic, strong) QSDictationHUD *hud;
@property (nonatomic, strong) NSStatusItem *statusItem;
@property (nonatomic) const QSHotkeySpec *hotkey;
@property (nonatomic, copy) NSString *dictationModel;
@property (nonatomic, copy) NSString *dictationLanguage;
// Names and terms from the settings, sent as the model's vocabulary hint.
@property (nonatomic, copy) NSString *dictationDictionary;
// "hold" or "toggle"; ids match DICTATION_MODES in the server's config.
@property (nonatomic, copy) NSString *dictationMode;
@property (nonatomic) NSTimeInterval maximumRecordingSeconds;
// Toggle mode: when the starting press began, to tell a tap from a hold.
@property (nonatomic, strong) NSDate *pressStartedAt;
// Toggle mode: the release of the press that stopped a recording is not a
// new instruction.
@property (nonatomic) BOOL ignoreNextRelease;
@property (nonatomic, strong) NSTimer *elapsedTimer;
@property (nonatomic) BOOL serverReachable;
@property (nonatomic) BOOL serverTransitionInProgress;
@property (atomic) BOOL shuttingDown;
@property (nonatomic) BOOL hotkeyIsDown;
@property (nonatomic) BOOL busy;
// The HUD state last shown from a job poll, so a poll every half second
// does not restart the animation every half second.
@property (nonatomic) NSInteger polledState;
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
    [self launchLocalServerOpeningBrowser:YES];
}

- (void)launchLocalServerOpeningBrowser:(BOOL)openBrowser {
    NSString *script = [NSBundle.mainBundle pathForResource:@"launch-server" ofType:@"sh"];
    if (!script) {
        [self reportFailure:@"The local server launcher is missing"];
        return;
    }

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:@"/bin/bash"];
    task.arguments = @[script];
    if (!openBrowser) {
        // A menu-bar restart should not yank a browser tab into focus.
        NSMutableDictionary *environment =
            [NSProcessInfo.processInfo.environment mutableCopy];
        environment[@"QS_NO_OPEN"] = @"1";
        task.environment = environment;
    }
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        [self reportFailure:[NSString stringWithFormat:@"Could not start the local server: %@",
                             error.localizedDescription ?: @"unknown error"]];
    }
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    self.hotkey = &QSHotkeyTable[0];
    self.dictationModel = @"1.7b";
    self.dictationLanguage = @"auto";
    self.dictationDictionary = @"";
    self.dictationMode = @"hold";
    self.maximumRecordingSeconds = QSDefaultMaximumRecordingSeconds;
    self.hud = [[QSDictationHUD alloc] init];
    [self writeProcessIdentity];
    [self installTerminationHandler];
    [self sweepStrandedRecordings];
    [self installStatusItem];

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
    [self tearDown];
}

/// Shared cleanup. Runs for a normal quit and for the SIGTERM that
/// "Stop Qwen Scribe" sends, which never reaches applicationWillTerminate:.
- (void)tearDown {
    self.shuttingDown = YES;
    [self.recorder stop];
    self.recorder = nil;
    [self.hud hide];
    if (self.globalMonitor) [NSEvent removeMonitor:self.globalMonitor];
    if (self.localMonitor) [NSEvent removeMonitor:self.localMonitor];
    self.globalMonitor = nil;
    self.localMonitor = nil;
    [self.heartbeatTimer invalidate];
    [self.recordingWatchdog invalidate];
    [self.elapsedTimer invalidate];
    if (self.recordingURL) {
        [[NSFileManager defaultManager] removeItemAtURL:self.recordingURL error:nil];
        self.recordingURL = nil;
    }
    [self removeProcessIdentity];
}

- (void)installTerminationHandler {
    // The default SIGTERM disposition would kill us before any cleanup runs,
    // leaving raw dictation audio in the temp directory.
    signal(SIGTERM, SIG_IGN);
    __weak typeof(self) weakSelf = self;
    self.terminationSource = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGTERM, 0,
                                                    dispatch_get_main_queue());
    dispatch_source_set_event_handler(self.terminationSource, ^{
        [weakSelf tearDown];
        exit(0);
    });
    dispatch_resume(self.terminationSource);
}

/// Remove dictation WAVs stranded by an earlier hard kill.
- (void)sweepStrandedRecordings {
    NSString *temporaryDirectory = NSTemporaryDirectory();
    NSArray<NSString *> *names = [NSFileManager.defaultManager
        contentsOfDirectoryAtPath:temporaryDirectory error:nil];
    for (NSString *name in names) {
        if (![name hasPrefix:@"qwen-scribe-dictation-"]) continue;
        [NSFileManager.defaultManager
            removeItemAtPath:[temporaryDirectory stringByAppendingPathComponent:name] error:nil];
    }
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
    if (event.keyCode != self.hotkey->keyCode) return;
    BOOL isDown = (event.modifierFlags & self.hotkey->mask) != 0;
    if (isDown == self.hotkeyIsDown) return;
    self.hotkeyIsDown = isDown;
    if (![self.dictationMode isEqualToString:@"toggle"]) {
        if (isDown) [self beginRecording];
        else [self finishRecording];
        return;
    }
    // Toggle: a tap starts, the next tap stops. A press held longer than a
    // tap still behaves as hold-to-talk, so the two modes share muscle memory.
    if (isDown) {
        if (self.recorder) {
            [self finishRecording];
            self.ignoreNextRelease = YES;
        } else {
            self.pressStartedAt = [NSDate date];
            [self beginRecording];
        }
        return;
    }
    if (self.ignoreNextRelease) {
        self.ignoreNextRelease = NO;
        return;
    }
    if (self.recorder && self.pressStartedAt
        && -[self.pressStartedAt timeIntervalSinceNow] >= QSToggleTapSeconds) {
        [self finishRecording];   // held, not tapped
    }
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
    // prepareToRecord already creates the file, so record it before the
    // failure check or the shared cleanup path will not know to delete it.
    self.recordingURL = url;
    [recorder prepareToRecord];
    if (error || ![recorder record]) {
        [self reportFailure:[NSString stringWithFormat:@"Could not start recording: %@", error.localizedDescription ?: @"unknown error"]];
        return;
    }

    self.recorder = recorder;
    self.recordingStartedAt = [NSDate date];
    self.targetApplication = NSWorkspace.sharedWorkspace.frontmostApplication;
    self.busy = YES;
    [self.hud showState:QSHUDStateListening];
    [self playSound:@"Tink"];

    __weak typeof(self) weakSelf = self;
    // If the key-up never arrives, or a toggle is never ended, stop on our
    // own rather than record forever.
    [self.recordingWatchdog invalidate];
    self.recordingWatchdog = [NSTimer scheduledTimerWithTimeInterval:self.maximumRecordingSeconds
                                                             repeats:NO
                                                               block:^(NSTimer *timer) {
        typeof(self) strongSelf = weakSelf;
        if (!strongSelf.recorder) return;
        strongSelf.hotkeyIsDown = NO;
        strongSelf.ignoreNextRelease = NO;
        [strongSelf finishRecording];
    }];
    // A toggled recording has no held key to remind the user it is running,
    // so the HUD counts the seconds instead.
    [self.elapsedTimer invalidate];
    self.elapsedTimer = nil;
    if ([self.dictationMode isEqualToString:@"toggle"]) {
        self.elapsedTimer = [NSTimer scheduledTimerWithTimeInterval:1.0
                                                            repeats:YES
                                                              block:^(NSTimer *timer) {
            typeof(self) strongSelf = weakSelf;
            if (!strongSelf.recorder || !strongSelf.recordingStartedAt) return;
            NSInteger seconds = (NSInteger)(-[strongSelf.recordingStartedAt timeIntervalSinceNow]);
            strongSelf.hud.view.detail = [NSString stringWithFormat:@"%ld:%02ld",
                                          (long)(seconds / 60), (long)(seconds % 60)];
            strongSelf.hud.view.needsDisplay = YES;
        }];
    }
}

- (void)finishRecording {
    [self.recordingWatchdog invalidate];
    self.recordingWatchdog = nil;
    [self.elapsedTimer invalidate];
    self.elapsedTimer = nil;
    self.pressStartedAt = nil;
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
    self.polledState = QSHUDStateTranscribing;
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
    appendField(@"model", self.dictationModel ?: @"1.7b");
    appendField(@"language", self.dictationLanguage ?: @"auto");
    appendField(@"timestamps", @"false");
    appendField(@"turbo", @"false");
    appendField(@"context", self.dictationDictionary ?: @"");
    // Lets the server apply the dictation-only choices, such as keeping
    // dictations out of history.
    appendField(@"source", @"dictation");
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
        if (weakSelf.shuttingDown) return;
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
    if (self.shuttingDown) return;
    if (attempt >= 1200) {
        [self reportFailure:@"Dictation timed out"];
        return;
    }
    NSURL *url = [NSURL URLWithString:[NSString stringWithFormat:@"%@/api/jobs/%@", QSServerBase, jobID]];
    __weak typeof(self) weakSelf = self;
    [[NSURLSession.sharedSession dataTaskWithURL:url
                              completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        if (weakSelf.shuttingDown) return;
        NSHTTPURLResponse *http = (NSHTTPURLResponse *)response;
        id payload = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        NSDictionary *state = [payload isKindOfClass:NSDictionary.class] ? payload : nil;
        if (error || !state) {
            [weakSelf reportFailure:@"Lost connection to Qwen Scribe"];
            return;
        }
        // A restarted server has forgotten the job; without this the helper
        // would treat the 404 body as "still running" and hang for 10 minutes.
        if (http.statusCode < 200 || http.statusCode >= 300) {
            [weakSelf reportFailure:state[@"detail"] ?: @"Qwen Scribe lost track of this dictation"];
            return;
        }
        NSString *status = state[@"status"];
        if ([status isEqualToString:@"done"]) {
            NSDictionary *result = [state[@"result"] isKindOfClass:NSDictionary.class] ? state[@"result"] : nil;
            NSString *text = [result[@"text"] isKindOfClass:NSString.class] ? result[@"text"] : nil;
            text = [text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
            if (text.length == 0) {
                [weakSelf reportFailure:@"No speech detected"];
                return;
            }
            dispatch_async(dispatch_get_main_queue(), ^{ [weakSelf pasteText:text]; });
        } else if ([status isEqualToString:@"error"]) {
            [weakSelf reportFailure:state[@"detail"] ?: @"Transcription failed"];
        } else {
            // Loading the model, or downloading it the first time, is the one
            // wait long enough to deserve its own words.
            QSHUDState shown = [status isEqualToString:@"loading"] ? QSHUDStateLoading : QSHUDStateTranscribing;
            NSString *detail = [state[@"detail"] isKindOfClass:NSString.class] ? state[@"detail"] : nil;
            dispatch_async(dispatch_get_main_queue(), ^{ [weakSelf showPolledState:shown detail:detail]; });
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)),
                           dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
                [weakSelf pollJob:jobID attempt:attempt + 1];
            });
        }
    }] resume];
}

- (void)showPolledState:(QSHUDState)state detail:(NSString *)detail {
    if (self.shuttingDown || !self.busy) return;
    if (self.polledState != state) {
        [self.hud showState:state];
        self.polledState = state;
    }
    // "Downloading model · 1.2 of 3.4 GB" carries the only figure worth
    // showing beside the label.
    NSString *figure = nil;
    NSRange separator = [detail rangeOfString:@"· "];
    if (state == QSHUDStateLoading && separator.location != NSNotFound) {
        figure = [detail substringFromIndex:NSMaxRange(separator)];
    }
    BOOL changed = (figure || self.hud.view.detail) && ![figure isEqualToString:self.hud.view.detail];
    if (changed) {
        self.hud.view.detail = figure;
        self.hud.view.needsDisplay = YES;
    }
}

/// Leave the transcript on the clipboard and tell the user why it was not typed.
- (void)deliverToClipboardOnly:(NSString *)text reason:(NSString *)reason {
    NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
    [pasteboard clearContents];
    [pasteboard setString:text forType:NSPasteboardTypeString];
    fprintf(stderr, "Qwen Scribe dictation: %s — transcript copied to the clipboard instead\n",
            reason.UTF8String);
    [self playSound:@"Basso"];
    [self.hud showState:QSHUDStateError];
    if (self.recordingURL) {
        [[NSFileManager defaultManager] removeItemAtURL:self.recordingURL error:nil];
    }
    self.recordingURL = nil;
    self.targetApplication = nil;
    self.busy = NO;
}

- (void)pasteText:(NSString *)text {
    if (self.shuttingDown) return;
    // Without Accessibility the synthetic Command-V is silently discarded, so
    // reporting "Text inserted" would be a lie and the transcript would be
    // lost when the pasteboard is restored a moment later.
    if (!AXIsProcessTrusted()) {
        NSDictionary *options = @{(__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES};
        AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)options);
        [self deliverToClipboardOnly:text reason:@"Accessibility access is not granted"];
        return;
    }
    if (!self.targetApplication || self.targetApplication.isTerminated) {
        [self deliverToClipboardOnly:text reason:@"the target application is gone"];
        return;
    }

    [self.targetApplication activateWithOptions:NSApplicationActivateAllWindows];
    __weak typeof(self) weakSelf = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.15 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        if (weakSelf.shuttingDown) return;
        NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
        NSArray *snapshot = [weakSelf snapshotPasteboard:pasteboard];
        [pasteboard clearContents];
        [pasteboard setString:text forType:NSPasteboardTypeString];
        NSInteger injectedChangeCount = pasteboard.changeCount;

        [weakSelf postPasteShortcutWithAttempt:0
                                    pasteboard:pasteboard
                           expectedChangeCount:injectedChangeCount
                             targetApplication:weakSelf.targetApplication
                                    completion:^(BOOL posted) {
            typeof(self) strongSelf = weakSelf;
            if (!strongSelf) return;
            if (posted) {
                [strongSelf playSound:@"Glass"];
                [strongSelf.hud showState:QSHUDStateInserted];

                // Restore only after Command-V was actually posted. If Quit
                // interrupts a modifier-delayed retry, leaving the transcript
                // on the clipboard is the only recoverable outcome.
                if (snapshot.count) {
                    dispatch_after(
                        dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.5 * NSEC_PER_SEC)),
                        dispatch_get_main_queue(), ^{
                            if (pasteboard.changeCount == injectedChangeCount) {
                                [strongSelf restorePasteboard:snapshot to:pasteboard];
                            }
                        });
                }
            } else if (!strongSelf.shuttingDown) {
                fprintf(stderr, "Qwen Scribe dictation: paste cancelled because focus, clipboard, or modifiers changed\n");
                [strongSelf playSound:@"Basso"];
                [strongSelf.hud showState:QSHUDStateError];
            }
            if (strongSelf.recordingURL) {
                [[NSFileManager defaultManager] removeItemAtURL:strongSelf.recordingURL error:nil];
            }
            strongSelf.recordingURL = nil;
            strongSelf.targetApplication = nil;
            strongSelf.busy = NO;
        }];
    });
}

/// Post Command-V, waiting briefly for any other physically held modifier to
/// clear — the window server ORs hardware modifiers into synthetic events, so
/// a held Shift or Option would turn the paste into a different shortcut. Each
/// retry also verifies that focus and clipboard ownership have not changed.
- (void)postPasteShortcutWithAttempt:(NSInteger)attempt
                          pasteboard:(NSPasteboard *)pasteboard
                 expectedChangeCount:(NSInteger)expectedChangeCount
                   targetApplication:(NSRunningApplication *)targetApplication
                          completion:(void (^)(BOOL posted))completion {
    NSRunningApplication *frontmost = NSWorkspace.sharedWorkspace.frontmostApplication;
    if (self.shuttingDown
        || pasteboard.changeCount != expectedChangeCount
        || !targetApplication
        || targetApplication.isTerminated
        || frontmost.processIdentifier != targetApplication.processIdentifier) {
        completion(NO);
        return;
    }
    const CGEventFlags blocking = kCGEventFlagMaskShift | kCGEventFlagMaskControl |
                                  kCGEventFlagMaskAlternate | kCGEventFlagMaskSecondaryFn;
    if (CGEventSourceFlagsState(kCGEventSourceStateCombinedSessionState) & blocking) {
        if (attempt >= 8) {
            completion(NO);
            return;
        }
        __weak typeof(self) weakSelf = self;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.15 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            [weakSelf postPasteShortcutWithAttempt:attempt + 1
                                         pasteboard:pasteboard
                                expectedChangeCount:expectedChangeCount
                                  targetApplication:targetApplication
                                         completion:completion];
        });
        return;
    }

    CGEventSourceRef source = CGEventSourceCreate(kCGEventSourceStatePrivate);
    CGEventRef keyDown = CGEventCreateKeyboardEvent(source, QSPasteKeyCode, true);
    CGEventRef keyUp = CGEventCreateKeyboardEvent(source, QSPasteKeyCode, false);
    if (!keyDown || !keyUp) {
        if (keyDown) CFRelease(keyDown);
        if (keyUp) CFRelease(keyUp);
        if (source) CFRelease(source);
        completion(NO);
        return;
    }
    CGEventSetFlags(keyDown, kCGEventFlagMaskCommand);
    CGEventSetFlags(keyUp, kCGEventFlagMaskCommand);
    CGEventPost(kCGHIDEventTap, keyDown);
    CGEventPost(kCGHIDEventTap, keyUp);
    CFRelease(keyDown);
    CFRelease(keyUp);
    if (source) CFRelease(source);
    completion(YES);
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
    [self fetchSettings];
}

// ── Settings (owned by the server; the helper is a follower) ──────────────

- (void)fetchSettings {
    NSURL *url = [NSURL URLWithString:[QSServerBase stringByAppendingString:@"/api/settings"]];
    __weak typeof(self) weakSelf = self;
    [[NSURLSession.sharedSession dataTaskWithURL:url
                              completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        NSHTTPURLResponse *http = (NSHTTPURLResponse *)response;
        id payload = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        // Any HTTP response means the server is up; only a 200 carries
        // settings (an older server without /api/settings still answers 404).
        BOOL reachable = !error && http.statusCode > 0;
        BOOL ok = reachable && http.statusCode == 200 && [payload isKindOfClass:NSDictionary.class];
        dispatch_async(dispatch_get_main_queue(), ^{
            typeof(self) strongSelf = weakSelf;
            if (!strongSelf) return;
            strongSelf.serverReachable = reachable;
            if (ok) [strongSelf applyDictationSettings:((NSDictionary *)payload)[@"dictation"]];
        });
    }] resume];
}

- (void)applyDictationSettings:(NSDictionary *)dictation {
    if (![dictation isKindOfClass:NSDictionary.class]) return;
    NSString *model = dictation[@"model"];
    if ([model isKindOfClass:NSString.class]) self.dictationModel = model;
    NSString *language = dictation[@"language"];
    if ([language isKindOfClass:NSString.class]) self.dictationLanguage = language;
    NSString *dictionary = dictation[@"dictionary"];
    if ([dictionary isKindOfClass:NSString.class]) self.dictationDictionary = dictionary;
    NSString *mode = dictation[@"mode"];
    // Like the key, never changed under an active recording: the gesture
    // that started it must be the one that stops it.
    if ([mode isKindOfClass:NSString.class] && !self.recorder) self.dictationMode = mode;
    NSNumber *limit = dictation[@"max_seconds"];
    if ([limit isKindOfClass:NSNumber.class]) {
        self.maximumRecordingSeconds = MIN(MAX(limit.doubleValue, QSMinimumRecordingLimit),
                                           QSMaximumRecordingLimit);
    }
    NSString *hotkeyIdentifier = dictation[@"hotkey"];
    if ([hotkeyIdentifier isKindOfClass:NSString.class]) {
        const QSHotkeySpec *spec = QSHotkeyForIdentifier(hotkeyIdentifier);
        // Never swap the key out from under an active recording — the old
        // key's release must still be the thing that stops the microphone.
        if (spec != self.hotkey && !self.recorder) {
            self.hotkey = spec;
            self.hotkeyIsDown = NO;
        }
    }
}

- (void)pushDictationSetting:(NSString *)key value:(NSString *)value {
    NSURL *url = [NSURL URLWithString:[QSServerBase stringByAppendingString:@"/api/settings"]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.HTTPMethod = @"PUT";
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    request.HTTPBody = [NSJSONSerialization dataWithJSONObject:@{@"dictation": @{key: value}}
                                                       options:0 error:nil];
    __weak typeof(self) weakSelf = self;
    [[NSURLSession.sharedSession dataTaskWithRequest:request
                                   completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        NSHTTPURLResponse *http = (NSHTTPURLResponse *)response;
        id payload = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{
            typeof(self) strongSelf = weakSelf;
            if (!strongSelf) return;
            if (!error && http.statusCode == 200 && [payload isKindOfClass:NSDictionary.class]) {
                [strongSelf applyDictationSettings:((NSDictionary *)payload)[@"dictation"]];
            } else {
                [strongSelf playSound:@"Basso"];
            }
        });
    }] resume];
}

// ── Menu bar ──────────────────────────────────────────────────────────────

- (void)installStatusItem {
    self.statusItem = [NSStatusBar.systemStatusBar statusItemWithLength:NSSquareStatusItemLength];
    NSImage *icon = [NSImage imageWithSystemSymbolName:@"waveform.circle"
                              accessibilityDescription:@"Qwen Scribe"];
    if (icon) {
        self.statusItem.button.image = icon;
    } else {
        self.statusItem.button.title = @"QS";
    }
    NSMenu *menu = [[NSMenu alloc] init];
    menu.delegate = self;
    menu.autoenablesItems = NO;
    self.statusItem.menu = menu;
}

- (NSString *)statusLineTitle {
    if (!self.serverReachable) return @"Server: starting…";
    BOOL accessibility = AXIsProcessTrusted();
    BOOL inputMonitoring = CGPreflightListenEventAccess();
    BOOL microphone = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] == AVAuthorizationStatusAuthorized;
    if (accessibility && inputMonitoring && microphone) {
        BOOL toggle = [self.dictationMode isEqualToString:@"toggle"];
        return [NSString stringWithFormat:@"Dictation ready — %@ %@",
                toggle ? @"press" : @"hold", @(self.hotkey->label)];
    }
    return @"Dictation: grant access in System Settings";
}

- (void)menuNeedsUpdate:(NSMenu *)menu {
    [menu removeAllItems];

    NSMenuItem *status = [[NSMenuItem alloc] initWithTitle:[self statusLineTitle]
                                                    action:nil keyEquivalent:@""];
    status.enabled = NO;
    [menu addItem:status];
    [menu addItem:NSMenuItem.separatorItem];

    NSMenuItem *open = [[NSMenuItem alloc] initWithTitle:@"Open Qwen Scribe"
                                                  action:@selector(openInterface:) keyEquivalent:@""];
    open.target = self;
    [menu addItem:open];

    NSMenuItem *hotkeyRoot = [[NSMenuItem alloc] initWithTitle:@"Push-to-Talk Key"
                                                        action:nil keyEquivalent:@""];
    NSMenu *hotkeyMenu = [[NSMenu alloc] init];
    hotkeyMenu.autoenablesItems = NO;
    for (size_t i = 0; i < sizeof(QSHotkeyTable) / sizeof(QSHotkeyTable[0]); i++) {
        const QSHotkeySpec *spec = &QSHotkeyTable[i];
        NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:@(spec->label)
                                                      action:@selector(selectHotkey:) keyEquivalent:@""];
        item.target = self;
        item.representedObject = @(spec->identifier);
        item.state = (spec == self.hotkey) ? NSControlStateValueOn : NSControlStateValueOff;
        item.enabled = self.serverReachable;
        [hotkeyMenu addItem:item];
    }
    hotkeyRoot.submenu = hotkeyMenu;
    [menu addItem:hotkeyRoot];

    NSMenuItem *modeRoot = [[NSMenuItem alloc] initWithTitle:@"Dictation Mode"
                                                      action:nil keyEquivalent:@""];
    NSMenu *modeMenu = [[NSMenu alloc] init];
    modeMenu.autoenablesItems = NO;
    NSArray<NSArray<NSString *> *> *modes = @[
        @[@"hold", @"Hold to Talk"],
        @[@"toggle", @"Press to Start, Press to Stop"],
    ];
    for (NSArray<NSString *> *entry in modes) {
        NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:entry[1]
                                                      action:@selector(selectMode:) keyEquivalent:@""];
        item.target = self;
        item.representedObject = entry[0];
        item.state = [self.dictationMode isEqualToString:entry[0]] ? NSControlStateValueOn : NSControlStateValueOff;
        item.enabled = self.serverReachable;
        [modeMenu addItem:item];
    }
    modeRoot.submenu = modeMenu;
    [menu addItem:modeRoot];

    [menu addItem:NSMenuItem.separatorItem];
    NSMenuItem *restart = [[NSMenuItem alloc] initWithTitle:@"Restart Server"
                                                     action:@selector(restartServer:) keyEquivalent:@""];
    restart.target = self;
    restart.enabled = !self.serverTransitionInProgress;
    [menu addItem:restart];
    NSMenuItem *quit = [[NSMenuItem alloc] initWithTitle:@"Quit Qwen Scribe"
                                                  action:@selector(quitQwenScribe:) keyEquivalent:@"q"];
    quit.target = self;
    quit.enabled = !self.serverTransitionInProgress;
    [menu addItem:quit];
}

- (void)openInterface:(id)sender {
    [NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:QSServerBase]];
}

- (void)selectHotkey:(NSMenuItem *)sender {
    NSString *identifier = sender.representedObject;
    if ([identifier isKindOfClass:NSString.class]) {
        [self pushDictationSetting:@"hotkey" value:identifier];
    }
}

- (void)selectMode:(NSMenuItem *)sender {
    NSString *identifier = sender.representedObject;
    if ([identifier isKindOfClass:NSString.class]) {
        [self pushDictationSetting:@"mode" value:identifier];
    }
}

// ── Managed server control ────────────────────────────────────────────────

/// The server's pid, but only when that pid still belongs to Qwen Scribe's
/// own runtime — a recycled pid must never be signalled.
- (pid_t)managedServerProcessIdentifier {
    NSString *support = [NSHomeDirectory() stringByAppendingPathComponent:
        @"Library/Application Support/Qwen Scribe"];
    NSString *pidfile = [support stringByAppendingPathComponent:@"server.pid"];
    NSString *contents = [NSString stringWithContentsOfFile:pidfile
                                                   encoding:NSUTF8StringEncoding error:nil];
    pid_t pid = (pid_t)contents.integerValue;
    if (pid <= 0) return 0;
    NSTask *ps = [[NSTask alloc] init];
    ps.executableURL = [NSURL fileURLWithPath:@"/bin/ps"];
    ps.arguments = @[@"-p", [NSString stringWithFormat:@"%d", pid], @"-o", @"command="];
    NSPipe *pipe = NSPipe.pipe;
    ps.standardOutput = pipe;
    if (![ps launchAndReturnError:nil]) return 0;
    [ps waitUntilExit];
    NSString *command = [[NSString alloc] initWithData:[pipe.fileHandleForReading readDataToEndOfFile]
                                              encoding:NSUTF8StringEncoding];
    if (![command containsString:@"/Library/Application Support/Qwen Scribe/"]) return 0;
    return pid;
}

- (void)stopManagedServerThen:(void (^)(void))completion {
    pid_t pid = [self managedServerProcessIdentifier];
    if (pid > 0) kill(pid, SIGTERM);
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        for (int i = 0; i < 40 && pid > 0 && kill(pid, 0) == 0; i++) {
            usleep(200000);   // up to 8 s for a graceful exit
        }
        if (pid > 0 && kill(pid, 0) == 0) {
            // A wedged MLX decode ignores SIGTERM until its chunk finishes;
            // mirror stop.sh and force the exit rather than hang the menu.
            kill(pid, SIGKILL);
            usleep(500000);
        }
        dispatch_async(dispatch_get_main_queue(), completion);
    });
}

- (void)restartServer:(id)sender {
    if (self.serverTransitionInProgress) return;
    self.serverTransitionInProgress = YES;
    self.serverReachable = NO;
    __weak typeof(self) weakSelf = self;
    [self stopManagedServerThen:^{
        [weakSelf launchLocalServerOpeningBrowser:NO];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.0 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            weakSelf.serverTransitionInProgress = NO;
            [weakSelf sendHeartbeat];
        });
    }];
}

- (void)quitQwenScribe:(id)sender {
    if (self.serverTransitionInProgress) return;
    self.serverTransitionInProgress = YES;
    // Stop an active microphone immediately; waiting for a wedged server can
    // otherwise keep recording for up to eight seconds after the user quits.
    [self tearDown];
    [self stopManagedServerThen:^{
        [NSApp terminate:nil];
    }];
}

- (void)reportFailure:(NSString *)message {
    fprintf(stderr, "Qwen Scribe dictation: %s\n", message.UTF8String);
    __weak typeof(self) weakSelf = self;
    dispatch_async(dispatch_get_main_queue(), ^{
        typeof(self) strongSelf = weakSelf;
        if (!strongSelf || strongSelf.shuttingDown) return;
        [strongSelf playSound:@"Basso"];
        [strongSelf.hud showState:QSHUDStateError];
        if (strongSelf.recordingURL) {
            [[NSFileManager defaultManager] removeItemAtURL:strongSelf.recordingURL error:nil];
        }
        [strongSelf.recordingWatchdog invalidate];
        strongSelf.recordingWatchdog = nil;
        [strongSelf.elapsedTimer invalidate];
        strongSelf.elapsedTimer = nil;
        strongSelf.pressStartedAt = nil;
        strongSelf.recordingURL = nil;
        strongSelf.recorder = nil;
        strongSelf.recordingStartedAt = nil;
        strongSelf.targetApplication = nil;
        strongSelf.busy = NO;
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
            NSRect frame = NSMakeRect(0, 0, QSHUDWidth, QSHUDHeight);
            QSHUDView *view = [[QSHUDView alloc] initWithFrame:frame];
            QSHUDState state = QSHUDStateListening;
            if (argc > 3 && strcmp(argv[3], "transcribing") == 0) {
                state = QSHUDStateTranscribing;
            } else if (argc > 3 && strcmp(argv[3], "loading") == 0) {
                state = QSHUDStateLoading;
            } else if (argc > 3 && strcmp(argv[3], "inserted") == 0) {
                state = QSHUDStateInserted;
            } else if (argc > 3 && strcmp(argv[3], "error") == 0) {
                state = QSHUDStateError;
            }
            [view showState:state];
            [view stopAnimating];
            view.phase = 1.15;
            NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc]
                initWithBitmapDataPlanes:NULL
                pixelsWide:(NSInteger)(QSHUDWidth * 2)
                pixelsHigh:(NSInteger)(QSHUDHeight * 2)
                bitsPerSample:8
                samplesPerPixel:4
                hasAlpha:YES
                isPlanar:NO
                colorSpaceName:NSCalibratedRGBColorSpace
                bitmapFormat:0
                bytesPerRow:0
                bitsPerPixel:0];
            bitmap.size = frame.size;
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

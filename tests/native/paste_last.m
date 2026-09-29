// Exercise the real delegate without recording, changing the clipboard, or
// posting keystrokes. Only the final paste and error presentation are spies.
#define main QwenScribeApplicationMain
#import "../../native/DictationHelper.m"
#undef main

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #condition); \
        return 1; \
    } \
} while (0)

@interface QSPasteSpy : QSDictationDelegate
@property (nonatomic, copy) NSString *pastedText;
@property (nonatomic, copy) NSString *cachedAtPaste;
@property (nonatomic, copy) NSString *failure;
@property (nonatomic, strong) NSRunningApplication *pastedTarget;
@property (nonatomic) NSInteger pasteCount;
@property (nonatomic) NSInteger soundCount;
@end

@implementation QSPasteSpy
- (void)pasteText:(NSString *)text {
    self.pasteCount += 1;
    self.pastedText = text;
    self.cachedAtPaste = self.lastDictationText;
    self.pastedTarget = self.targetApplication;
    // Model either a successful insertion or a failure: neither changes the
    // cache. The real bridge also returns the helper to idle after either.
    self.busy = NO;
}
- (void)reportFailure:(NSString *)message { self.failure = message; }
- (void)playSound:(NSString *)name { self.soundCount += 1; }
- (void)removeProcessIdentity {}  // Never touch the running app's pid file.
@end

int main(void) {
    @autoreleasepool {
        QSPasteSpy *delegate = [[QSPasteSpy alloc] init];
        [delegate pasteLastDictation:nil];
        CHECK(delegate.pasteCount == 0);
        CHECK(!delegate.busy);

        [delegate completeDictationWithText:@"  A corrected dictation.\n"];
        CHECK([delegate.lastDictationText isEqualToString:@"A corrected dictation."]);
        CHECK([delegate.cachedAtPaste isEqualToString:delegate.pastedText]);
        CHECK(delegate.pasteCount == 1);

        [delegate completeDictationWithText:@" \n\t"];
        [delegate completeDictationWithText:nil];
        [delegate reportFailure:@"Transcription failed"];
        CHECK([delegate.lastDictationText isEqualToString:@"A corrected dictation."]);
        CHECK(delegate.pasteCount == 1);

        NSRunningApplication *frontmost = NSWorkspace.sharedWorkspace.frontmostApplication;
        [delegate pasteLastDictation:nil];
        CHECK(delegate.pasteCount == 2);
        CHECK(delegate.pastedTarget.processIdentifier == frontmost.processIdentifier);
        [delegate pasteLastDictation:nil];
        CHECK(delegate.pasteCount == 3);
        CHECK([delegate.lastDictationText isEqualToString:delegate.pastedText]);

        delegate.busy = YES;
        delegate.targetApplication = NSRunningApplication.currentApplication;
        [delegate pasteLastDictation:nil];
        CHECK(delegate.busy);
        CHECK(delegate.targetApplication.processIdentifier == NSRunningApplication.currentApplication.processIdentifier);
        CHECK(delegate.pasteCount == 3);
        CHECK(delegate.soundCount == 1);
        delegate.busy = NO;
        delegate.serverTransitionInProgress = YES;
        [delegate pasteLastDictation:nil];
        CHECK(delegate.pasteCount == 3);
        delegate.serverTransitionInProgress = NO;

        [delegate completeDictationWithText:@"下一条 LuluCare"];
        CHECK([delegate.lastDictationText isEqualToString:@"下一条 LuluCare"]);
        CHECK(delegate.pasteCount == 4);

        // Dispatch the actual Carbon callback, without reserving a global
        // shortcut or sending synthetic keyboard input into the user's apps.
        EventRef event = NULL;
        CHECK(CreateEvent(NULL, kEventClassKeyboard, kEventHotKeyReleased,
                          0, kEventAttributeNone, &event) == noErr);
        EventHotKeyID identifier = {'QSLP', 1};
        CHECK(SetEventParameter(event, kEventParamDirectObject, typeEventHotKeyID,
                                sizeof(identifier), &identifier) == noErr);
        CHECK(QSPasteLastHotkeyHandler(NULL, event, (__bridge void *)delegate) == noErr);
        CHECK(delegate.pasteCount == 5);
        identifier.id = 2;
        SetEventParameter(event, kEventParamDirectObject, typeEventHotKeyID,
                          sizeof(identifier), &identifier);
        CHECK(QSPasteLastHotkeyHandler(NULL, event, (__bridge void *)delegate) == eventNotHandledErr);
        CHECK(delegate.pasteCount == 5);
        ReleaseEvent(event);

        [delegate tearDown];
        CHECK(delegate.lastDictationText == nil);
        [delegate completeDictationWithText:@"Late result after quit"];
        [delegate pasteLastDictation:nil];
        CHECK(delegate.lastDictationText == nil);
        CHECK(delegate.pasteCount == 5);
        puts("Paste-last-dictation native checks passed");
    }
    return 0;
}

import { describe, expect, it } from 'vitest';

import type { IRemotePlaybackState } from '@/services/RemoteControl';

import { TemporaryNHKHideAcknowledgement, TemporaryNHKHideGesture } from '@/services/RemoteNHKExclusion';

const state = (commandId: string, status: 'Applied' | 'Failed'): IRemotePlaybackState => ({
    content_type: 'Idle',
    supports_nhk_exclusion: true,
    nhk_exclusion_mode: 'OFF',
    nhk_exclusion_expires_at: null,
    nhk_exclusion_command_result: {command_id: commandId, status},
});

describe('temporary N〇K exclusion gesture', () => {
    it('requires five artwork taps no more than one second apart', () => {
        const gesture = new TemporaryNHKHideGesture();

        for (const now of [0, 1000, 2000, 3000]) expect(gesture.registerTap(now)).toBe(false);
        expect(gesture.registerTap(4000)).toBe(true);
    });

    it('resets the sequence after a gap or lifecycle reset', () => {
        const gesture = new TemporaryNHKHideGesture();

        for (const now of [0, 500, 1000, 1500]) expect(gesture.registerTap(now)).toBe(false);
        expect(gesture.registerTap(2501)).toBe(false);
        for (const now of [3000, 3500, 4000]) expect(gesture.registerTap(now)).toBe(false);
        gesture.reset();
        for (const now of [4500, 5000, 5500, 6000]) expect(gesture.registerTap(now)).toBe(false);
    });
});

describe('temporary N〇K exclusion acknowledgement', () => {
    it('waits when legacy or initial state omits the result', () => {
        const acknowledgement = new TemporaryNHKHideAcknowledgement();
        acknowledgement.begin('pending-command');
        expect(acknowledgement.consume({content_type: 'Idle'} as IRemotePlaybackState)).toBeNull();
        expect(acknowledgement.consume(state('pending-command', 'Applied'))).toBe('Applied');
    });
    it('only accepts the matching TV command result once', () => {
        const acknowledgement = new TemporaryNHKHideAcknowledgement();
        acknowledgement.begin('expected-command');

        expect(acknowledgement.consume(state('another-command', 'Applied'))).toBeNull();
        expect(acknowledgement.consume(state('expected-command', 'Applied'))).toBe('Applied');
        expect(acknowledgement.consume(state('expected-command', 'Applied'))).toBeNull();
    });

    it('reports an acknowledged failure and unconfirmed lifecycle separately', () => {
        const acknowledgement = new TemporaryNHKHideAcknowledgement();
        acknowledgement.begin('failed-command');
        expect(acknowledgement.consume(state('failed-command', 'Failed'))).toBe('Failed');

        acknowledgement.begin('unconfirmed-command');
        expect(acknowledgement.expire()).toBe(true);
        expect(acknowledgement.expire()).toBe(false);
    });
});

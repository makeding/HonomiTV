import type { IRemotePlaybackState } from '@/services/RemoteControl';

export type TemporaryNHKHideFeedback = 'Idle' | 'Waiting' | 'Applied' | 'Failed' | 'Unconfirmed';

/** 選択中テレビのアートワークでのみ有効な五連続タップを判定する。 */
export class TemporaryNHKHideGesture {
    private tapCount = 0;
    private lastTapAt: number | null = null;

    registerTap(now: number): boolean {
        if (this.lastTapAt === null || (now - this.lastTapAt) > 1000) {
            this.tapCount = 1;
        } else {
            this.tapCount += 1;
        }
        this.lastTapAt = now;
        if (this.tapCount < 5) return false;
        this.reset();
        return true;
    }

    reset(): void {
        this.tapCount = 0;
        this.lastTapAt = null;
    }
}

/** HTTP 配達 ID とテレビが State で報告した実行結果を厳密に対応付ける。 */
export class TemporaryNHKHideAcknowledgement {
    private commandId: string | null = null;

    begin(commandId: string): void {
        this.commandId = commandId;
    }

    consume(state: IRemotePlaybackState): TemporaryNHKHideFeedback | null {
        if (this.commandId === null) return null;
        const result = state.nhk_exclusion_command_result;
        if (result === null || result === undefined || result.command_id !== this.commandId) return null;
        this.commandId = null;
        return result.status === 'Applied' ? 'Applied' : 'Failed';
    }

    expire(): boolean {
        if (this.commandId === null) return false;
        this.commandId = null;
        return true;
    }

    reset(): void {
        this.commandId = null;
    }
}

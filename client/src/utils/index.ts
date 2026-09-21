
// day.js に毎回プラグインを設定するのが面倒かつ嵌まりポイントが多いので、ここでエクスポートする day.js を使う。
// API の ISO 8601 オフセットは実時刻として保持し、表示と番組表境界はブラウザを実行する端末のローカル時刻にする。

import dayjsOriginal from 'dayjs';
import ja from 'dayjs/locale/ja';
import duration from 'dayjs/plugin/duration';
import isBetween from 'dayjs/plugin/isBetween';
import isSameOrAfter from 'dayjs/plugin/isSameOrAfter';
import isSameOrBefore from 'dayjs/plugin/isSameOrBefore';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';

import type { ConfigType, Dayjs } from 'dayjs';

dayjsOriginal.extend(duration);
dayjsOriginal.extend(isBetween);
dayjsOriginal.extend(isSameOrAfter);
dayjsOriginal.extend(isSameOrBefore);
dayjsOriginal.extend(utc);
dayjsOriginal.extend(timezone);
dayjsOriginal.locale(ja);

export const dayjs = (date?: ConfigType): Dayjs => {
    return dayjsOriginal(date).local();
};
export { dayjsOriginal };


// 共通ユーティリティをデフォルトとしてインポート
import Utils from '@/utils/Utils';
export default Utils;

// Utils フォルダ配下のユーティリティを一括でインポートできるように
export * from '@/utils/ChannelUtils';
export * from '@/utils/CommentUtils';
export * from '@/utils/PlayerUtils';
export * from '@/utils/ProgramUtils';
export * from '@/utils/Semaphore';
export * from '@/utils/TweetUtils';

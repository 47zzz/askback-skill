// 動態的唯一來源：easing、spring、節奏。顏色不在這裡——顏色跟著原片（render.py 從 palette 算好塞進 props.style）。
import {Easing} from 'remotion';

export const theme = {
  ease: {
    out: Easing.bezier(0.16, 1, 0.3, 1),      // 進場
    inOut: Easing.bezier(0.83, 0, 0.17, 1),   // 移動
    in: Easing.bezier(0.7, 0, 0.84, 0),       // 退場（比進場快）
  },
  spring: {
    smooth: {damping: 20, stiffness: 90, mass: 1},     // 投影片的字、大塊元素
    snappy: {damping: 14, stiffness: 160, mass: 0.6},  // 標籤、小元素
    bouncy: {damping: 11, stiffness: 170, mass: 0.7},  // 角色劇的對話泡泡
  },
  // 同一句裡的多個元素依序出現（stagger），不要同時冒出來
  staggerFrames: 5,
  // 每句的「動作窗口」占旁白長度的比例與上下限（秒）：和 Pillow 引擎一致
  window: (dur: number) => Math.min(Math.max(0.5, dur * 0.55), 2.4),
  fonts: {
    hand: '"Iansui", "Noto Sans TC", sans-serif',
    sans: '"Noto Sans TC", sans-serif',
  },
} as const;

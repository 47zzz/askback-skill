import React from 'react';
import {AbsoluteFill, Audio, Easing, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Chrome} from './kit/Chrome';
import {mix} from './kit/color';
import {useFonts} from './kit/fonts';
import {Shape} from './kit/Shapes';
import {Runs} from './kit/Text';
import {theme} from './kit/theme';
import {Beat, LineItem, Props, Scene, ShapeItem} from './kit/types';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

/**
 * 資料驅動的畫面：render.py 已經排好版（字級、欄寬、圖區），這裡只負責「什麼時候、怎麼動」。
 * 規則：一句旁白＝一個畫面動作；同一句裡的多個元素依序出現；進場用 spring／bezier，不用線性。
 */
export const Lesson: React.FC<Props> = (p) => {
  useFonts();
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  if (!p.beats.length) return <AbsoluteFill style={{background: p.style.bg}} />;
  let bi = 0;
  while (bi + 1 < p.beats.length && t >= p.beats[bi + 1].start) bi++;
  const beat = p.beats[bi];
  const scene = p.scenes[beat.scene];
  const font = theme.fonts[p.style.font];
  // 換場：新場景淡入（第一個場景不用）
  const firstBeatOfScene = p.beats.find((b) => b.scene === beat.scene)!;
  const sceneIn = beat.scene === 0 ? 1 : interpolate(t, [firstBeatOfScene.start - 0.1, firstBeatOfScene.start + 0.25], [0, 1], {...clamp, easing: theme.ease.out});
  return (
    <AbsoluteFill style={{background: p.style.bg, fontFamily: font, overflow: 'hidden'}}>
      {p.audio && <Audio src={staticFile(p.audio)} />}
      <div style={{position: 'absolute', inset: 0, opacity: sceneIn}}>
        <SceneView p={p} scene={scene} bi={bi} />
      </div>
      <Chrome p={p} beat={beat} />
    </AbsoluteFill>
  );
};

/** 元素什麼時候出現：同一句的第 k 個元素，在這句的動作窗口裡分到一格。 */
const timingOf = (beat: Beat, k: number, n: number, fps: number) => {
  const win = theme.window(beat.dur);
  const slot = win / Math.max(1, n);
  const startF = Math.round(beat.start * fps) + Math.round(k * slot * fps);
  const durF = Math.max(Math.round(slot * fps), Math.round(0.3 * fps));
  return {startF, durF};
};

const SceneView: React.FC<{p: Props; scene: Scene; bi: number}> = ({p, scene, bi}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const st = p.style;
  const pal = st.colors;
  const font = theme.fonts[st.font];
  const story = st.form === 'story';
  // 最後一次 clear（在目前這句或更早）之後的項目
  let start = 0;
  scene.items.forEach((it, k) => {
    if (it.kind === 'clear' && it.beat <= bi) start = k + 1;
  });
  const visible = scene.items.slice(start).filter((it) => it.beat <= bi && it.kind !== 'clear');
  // 每句的元素各自編號（stagger 用）
  const perBeat: Record<number, number> = {};
  const indexed = visible.map((it) => {
    const k = perBeat[it.beat] ?? 0;
    perBeat[it.beat] = k + 1;
    return {it, k};
  });
  const counts = {...perBeat};

  const [x1, y1, x2, y2] = scene.text_rect ?? [56, 64, 1224, 592];
  const ys: Record<number, number> = {};
  const storySide: Record<string, number> = {};
  const nodes: React.ReactNode[] = [];
  const fig = scene.fig_box;

  indexed.forEach(({it, k}, idx) => {
    const beat = p.beats[it.beat];
    const n = counts[it.beat];
    const {startF, durF} = timingOf(beat, k, n, fps);
    const prog = interpolate(frame, [startF, startF + durF], [0, 1], clamp);
    const markP = interpolate(frame, [startF + durF, startF + durF + Math.round(0.35 * fps)], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
    if (it.kind === 'line') {
      const line = it as LineItem;
      const c = line.col;
      const cx = x1 + (c - 1) * (scene.col_w + 48);
      const y = ys[c] ?? y1;
      ys[c] = y + (story ? line.size * 1.9 : line.advance);
      let x = cx + line.indent * line.size;
      if (line.align === 'center' || scene.layout === 'figure') x = cx + (scene.col_w - line.width) / 2;
      if (story) {
        const who = line.who || '';
        if (!(who in storySide)) storySide[who] = Object.keys(storySide).length % 2;
        const side = storySide[who];
        const bw = line.width + 56;
        const bx0 = side === 0 ? cx : cx + scene.col_w - bw;
        const s = spring({frame: frame - startF, fps, config: theme.spring.bouncy});
        if (frame < startF) return;
        nodes.push(
          <div
            key={idx}
            style={{
              position: 'absolute', left: bx0, top: y, width: bw, height: line.size * 1.6, borderRadius: 22,
              background: mix(st.bg, pal[side === 0 ? 'b' : 'o'], 0.22), border: `2px solid ${mix(st.bg, st.ink, 0.5)}`,
              transform: `scale(${0.6 + 0.4 * s})`, transformOrigin: side === 0 ? 'left center' : 'right center', opacity: Math.min(1, s * 1.5),
              fontSize: line.size, lineHeight: `${line.size * 1.36}px`, whiteSpace: 'nowrap', paddingLeft: 28, paddingTop: line.size * 0.12, boxSizing: 'border-box',
            }}
          >
            <Runs runs={line.runs} markP={markP} bold={line.bold} />
          </div>,
        );
        return;
      }
      if (prog <= 0) return;
      const top = y + (line.tall ? line.size * 0.3 : 0);
      const base: React.CSSProperties = {
        position: 'absolute', left: x, top, fontSize: line.size, lineHeight: `${line.size * 1.3}px`, whiteSpace: 'nowrap',
        color: st.ink, fontWeight: line.bold ? 700 : undefined,
      };
      if (st.reveal === 'write') {
        // 板書：像筆從左寫到右，筆尖有一點光
        const pw = interpolate(prog, [0, 1], [0, 100], {...clamp, easing: Easing.linear});
        nodes.push(
          <div key={idx} style={base}>
            <div style={{clipPath: `inset(-25% ${100 - pw}% -25% -3%)`}}>
              <Runs runs={line.runs} markP={markP} bold={line.bold} />
            </div>
            {prog < 1 && (
              <div
                style={{
                  position: 'absolute', left: `${pw}%`, top: '55%', width: 9, height: 9, borderRadius: 5, background: pal.y ?? st.ink,
                  boxShadow: `0 0 10px 3px ${pal.y ?? st.ink}`, transform: 'translate(-50%,-50%)', opacity: 0.9,
                }}
              />
            )}
          </div>,
        );
      } else if (st.reveal === 'pop') {
        const s = spring({frame: frame - startF, fps, config: theme.spring.snappy});
        nodes.push(
          <div key={idx} style={{...base, transform: `scale(${0.85 + 0.15 * s})`, transformOrigin: 'left center', opacity: Math.min(1, s * 1.4)}}>
            <Runs runs={line.runs} markP={markP} bold={line.bold} />
          </div>,
        );
      } else {
        // 投影片／實驗：淡入 + 上浮 + 微放大，三個屬性一起動
        const s = spring({frame: frame - startF, fps, config: theme.spring.smooth});
        nodes.push(
          <div key={idx} style={{...base, opacity: s, transform: `translateY(${(1 - s) * 18}px) scale(${0.96 + 0.04 * s})`, transformOrigin: 'left center'}}>
            <Runs runs={line.runs} markP={markP} bold={line.bold} />
          </div>,
        );
      }
    } else if (it.kind === 'shape' && fig) {
      nodes.push(<Shape key={idx} it={it as ShapeItem} box={fig} p={prog} bg={st.bg} hasImage={Boolean(scene.image)} font={font} />);
    }
  });

  return (
    <>
      {scene.title && (
        <div style={{position: 'absolute', left: 56, top: 40 + (46 - scene.title.size) / 2, fontSize: scene.title.size, lineHeight: `${scene.title.size * 1.3}px`, whiteSpace: 'nowrap', fontWeight: 700, color: st.ink}}>
          <Runs runs={scene.title.runs} markP={1} bold />
        </div>
      )}
      {scene.title && st.form === 'slides' && (
        <div style={{position: 'absolute', left: 56, top: 105, width: p.w - 112, height: 3, background: mix(st.bg, st.ink, 0.25)}} />
      )}
      {fig && scene.image && (
        <>
          <Img src={staticFile(scene.image)} style={{position: 'absolute', left: fig[0], top: fig[1], width: fig[2] - fig[0], height: fig[3] - fig[1]}} />
          <div style={{position: 'absolute', left: fig[0] - 2, top: fig[1] - 2, width: fig[2] - fig[0] + 3, height: fig[3] - fig[1] + 3, border: `2px solid ${mix(st.bg, st.ink, 0.35)}`, boxSizing: 'border-box'}} />
        </>
      )}
      {scene.cols === 2 && st.form === 'board' && (
        <div style={{position: 'absolute', left: x1 + scene.col_w + 24, top: y1, width: 3, height: y2 - y1, background: mix(st.bg, pal.b, 0.7)}} />
      )}
      {nodes}
    </>
  );
};

import React from 'react';
import {Easing, interpolate, spring, useVideoConfig} from 'remotion';
import {Runs} from './Text';
import {ShapeItem} from './types';
import {theme} from './theme';

type Box = number[]; // [x1, y1, x2, y2] 畫面像素

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

/** 一個標記（圈、框、線、箭頭、多邊形、點、標籤）畫在圖區上。座標是圖區的 0–1 比例，和 Pillow 引擎一致。 */
export const Shape: React.FC<{it: ShapeItem; box: Box; p: number; bg: string; hasImage: boolean; font: string}> = ({it, box, p, bg, hasImage, font}) => {
  const {fps} = useVideoConfig();
  const fw = box[2] - box[0], fh = box[3] - box[1];
  const P = (xy: number[]) => [xy[0] * fw, xy[1] * fh];
  const wd = it.width ?? 5;
  const col = it.color;
  const draw = interpolate(p, [0, 1], [0, 1], {...clamp, easing: Easing.out(Easing.quad)});
  if (p <= 0) return null;
  const svgStyle: React.CSSProperties = {position: 'absolute', left: box[0], top: box[1], width: fw, height: fh, overflow: 'visible', pointerEvents: 'none'};
  const dashed = it.dash ? {strokeDasharray: '18 14'} : {};

  if (it.shape === 'label') {
    const [cx, cy] = P(it.at!);
    const sz = it.size ?? 36;
    const s = spring({frame: p * 14, fps, config: theme.spring.snappy});
    return (
      <div
        style={{
          position: 'absolute', left: box[0] + cx, top: box[1] + cy - sz * 0.7, transform: `translateX(-50%) scale(${0.6 + 0.4 * s})`,
          opacity: Math.min(1, s * 1.6), fontFamily: font, fontSize: sz, lineHeight: 1.4, whiteSpace: 'nowrap', fontWeight: 700,
          padding: hasImage || it.plate ? '0 10px' : 0, borderRadius: 8, background: hasImage || it.plate ? bg : 'transparent',
        }}
      >
        <Runs runs={it.runs ?? []} markP={p} bold />
      </div>
    );
  }
  if (it.shape === 'dot') {
    const [cx, cy] = P(it.at!);
    const r = (it.r ?? 0.012) * fw;
    const s = spring({frame: p * 12, fps, config: theme.spring.bouncy});
    return (
      <svg style={svgStyle} viewBox={`0 0 ${fw} ${fh}`}>
        <circle cx={cx} cy={cy} r={r * s} fill={col} />
      </svg>
    );
  }
  let d = '';
  let fill: string | undefined;
  let fillOpacity = 0;
  if (it.shape === 'circle') {
    const [cx, cy] = P(it.at!);
    const r = (it.r ?? 0.08) * fw;
    const ry = it.ry !== undefined ? it.ry * fh : r;
    d = `M ${cx} ${cy - ry} A ${r} ${ry} 0 1 1 ${cx} ${cy + ry} A ${r} ${ry} 0 1 1 ${cx} ${cy - ry}`;
  } else if (it.shape === 'box') {
    const [x1, y1] = P(it.at!.slice(0, 2)), [x2, y2] = P(it.at!.slice(2));
    d = `M ${x1} ${y1} L ${x2} ${y1} L ${x2} ${y2} L ${x1} ${y2} Z`;
    if (it.fill) { fill = it.fill; fillOpacity = draw; }
  } else if (it.shape === 'line' || it.shape === 'arrow') {
    const [x1, y1] = P(it.from!), [x2, y2] = P(it.to!);
    d = `M ${x1} ${y1} L ${x2} ${y2}`;
    const ang = Math.atan2(y2 - y1, x2 - x1), L = 9 + wd * 2.6;
    const head = `${x2 + Math.cos(ang) * L * 0.6},${y2 + Math.sin(ang) * L * 0.6} ${x2 - L * Math.cos(ang - 0.45)},${y2 - L * Math.sin(ang - 0.45)} ${x2 - L * Math.cos(ang + 0.45)},${y2 - L * Math.sin(ang + 0.45)}`;
    const headP = interpolate(p, [0.75, 1], [0, 1], clamp);
    return (
      <svg style={svgStyle} viewBox={`0 0 ${fw} ${fh}`}>
        <path d={d} pathLength={1} fill="none" stroke={col} strokeWidth={wd} strokeLinecap="round" {...(it.dash ? {} : {strokeDasharray: 1, strokeDashoffset: 1 - draw})}
          {...(it.dash ? {strokeDasharray: '18 14', opacity: draw} : {})} />
        {it.shape === 'arrow' && headP > 0 && (
          <polygon points={head} fill={col} style={{transformOrigin: `${x2}px ${y2}px`, transform: `scale(${headP})`}} />
        )}
      </svg>
    );
  } else if (it.shape === 'polygon') {
    const pts = it.points!.map(P);
    d = `M ${pts.map((q) => q.join(' ')).join(' L ')} Z`;
    if (it.fill) { fill = it.fill; fillOpacity = 0.55 * draw; }
  } else {
    return null;
  }
  return (
    <svg style={svgStyle} viewBox={`0 0 ${fw} ${fh}`}>
      {fill && <path d={d} fill={fill} fillOpacity={fillOpacity} stroke="none" />}
      <path d={d} pathLength={1} fill="none" stroke={col} strokeWidth={wd} strokeLinecap="round" strokeLinejoin="round"
        strokeDasharray={1} strokeDashoffset={1 - draw} {...dashed} />
    </svg>
  );
};

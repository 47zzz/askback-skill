import React from 'react';
import {Run} from './types';

/**
 * 把 draw.py 解析好的 runs 畫成 HTML：顏色、上下標、分數、圈／劃掉／底線（markP 是標記的進度 0–1，
 * 文字寫完後才開始畫標記，和 Pillow 引擎的行為一致）。
 */
export const Runs: React.FC<{runs: Run[]; markP: number; bold?: boolean}> = ({runs, markP, bold}) => {
  // 同一個 gid 的連續 runs 包成一組，標記畫在整組外面
  const groups: {gid: number | null; mark: string; color: string; runs: Run[]}[] = [];
  for (const r of runs) {
    const last = groups[groups.length - 1];
    if (last && r.gid !== null && last.gid === r.gid) last.runs.push(r);
    else groups.push({gid: r.gid, mark: r.mark, color: r.color, runs: [r]});
  }
  return (
    <span style={{fontWeight: bold ? 700 : undefined}}>
      {groups.map((g, i) => {
        const inner = g.runs.map((r, k) => <Piece key={k} run={r} markP={markP} bold={bold} />);
        if (!g.mark || g.gid === null) return <React.Fragment key={i}>{inner}</React.Fragment>;
        return (
          <Marked key={i} mark={g.mark} color={g.color} p={markP}>
            {inner}
          </Marked>
        );
      })}
    </span>
  );
};

const Piece: React.FC<{run: Run; markP: number; bold?: boolean}> = ({run, markP, bold}) => {
  if (run.frac) {
    return (
      <span
        style={{
          display: 'inline-flex', flexDirection: 'column', alignItems: 'center', verticalAlign: 'middle',
          fontSize: '0.8em', margin: '0 0.12em', lineHeight: 1.15, color: run.color,
        }}
      >
        <span style={{padding: '0 0.15em'}}><Runs runs={run.frac.num} markP={markP} bold={bold} /></span>
        <span style={{display: 'block', width: '100%', borderTop: `0.09em solid ${run.color}`}} />
        <span style={{padding: '0 0.15em'}}><Runs runs={run.frac.den} markP={markP} bold={bold} /></span>
      </span>
    );
  }
  const style: React.CSSProperties = {color: run.color};
  if (run.pos === '^') return <sup style={{...style, fontSize: '0.6em', verticalAlign: '0.55em', lineHeight: 0}}>{run.s}</sup>;
  if (run.pos === '_') return <sub style={{...style, fontSize: '0.6em', verticalAlign: '-0.3em', lineHeight: 0}}>{run.s}</sub>;
  return <span style={style}>{run.s}</span>;
};

/** 圈起來（!）、劃掉（~）、底線（_）：像老師在板書上用筆畫的，從左畫到右。 */
const Marked: React.FC<{mark: string; color: string; p: number; children: React.ReactNode}> = ({mark, color, p, children}) => (
  <span style={{position: 'relative', display: 'inline-block', whiteSpace: 'nowrap'}}>
    {children}
    {mark.includes('!') && (
      <svg
        style={{position: 'absolute', left: '-0.3em', top: '-0.12em', width: 'calc(100% + 0.6em)', height: 'calc(100% + 0.3em)', overflow: 'visible', pointerEvents: 'none'}}
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
      >
        <path
          d="M 50 2 A 48 48 0 1 1 50 98 A 48 48 0 1 1 50 2"
          pathLength={1}
          fill="none"
          stroke={color}
          strokeWidth={3}
          vectorEffect="non-scaling-stroke"
          strokeLinecap="round"
          strokeDasharray={1}
          strokeDashoffset={1 - Math.max(0, Math.min(1, p))}
        />
      </svg>
    )}
    {mark.includes('~') && (
      <span
        style={{
          position: 'absolute', left: '-0.08em', right: '-0.08em', top: '0.62em', height: '0.07em', background: color,
          transform: `rotate(-4deg) scaleX(${Math.max(0, Math.min(1, p))})`, transformOrigin: 'left center', pointerEvents: 'none',
        }}
      />
    )}
    {mark.includes('_') && (
      <span
        style={{
          position: 'absolute', left: 0, right: 0, bottom: '-0.06em', height: '0.07em', background: color,
          transform: `scaleX(${Math.max(0, Math.min(1, p))})`, transformOrigin: 'left center', pointerEvents: 'none',
        }}
      />
    )}
  </span>
);

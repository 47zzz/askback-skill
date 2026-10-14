import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {mix} from './color';
import {Beat, Props} from './types';
import {theme} from './theme';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

/** 每一格都有的東西：AI 標示、字幕、來源。和 Pillow 引擎的 chrome() 對齊。 */
export const Chrome: React.FC<{p: Props; beat: Beat}> = ({p, beat}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {style, w, h, subH} = p;
  const bg = style.bg, ink = style.ink;
  const t = frame / fps;
  const local = t - beat.start;
  const showUntil = beat.start + beat.dur + 0.25;
  const subOpacity = local < 0 ? 0 : interpolate(t, [beat.start, beat.start + 4 / fps, showUntil - 4 / fps, showUntil], [0, 1, 1, 0], clamp);
  const bandFill = style.form !== 'slides' ? mix(bg, '#000000', 0.55) : mix(bg, ink, 0.9);
  const sub = (beat.who ? `${beat.who}：` : '') + beat.sub;
  return (
    <>
      <div
        style={{
          position: 'absolute', top: 14, right: 16, height: 36, padding: '0 14px', borderRadius: 18, display: 'flex', alignItems: 'center',
          background: mix(bg, ink, 0.14), border: `1px solid ${mix(bg, ink, 0.45)}`, color: ink,
          fontFamily: theme.fonts.sans, fontSize: 20, fontWeight: 700, letterSpacing: 0.5, zIndex: 100,
        }}
      >
        {p.badge}
      </div>
      <div style={{position: 'absolute', left: 0, top: h - subH, width: w, height: subH, background: bandFill, zIndex: 90}} />
      {beat.sub && (
        <div
          style={{
            position: 'absolute', left: 60, width: w - 120, top: h - subH, height: subH - 22, display: 'flex', alignItems: 'center',
            justifyContent: 'center', textAlign: 'center', color: '#ffffff', fontFamily: theme.fonts.sans, fontSize: 32, fontWeight: 700,
            lineHeight: '40px', opacity: subOpacity, zIndex: 95, overflow: 'hidden',
          }}
        >
          <span style={{display: 'block', maxHeight: 80}}>{sub}</span>
        </div>
      )}
      {p.credit && (
        <div style={{position: 'absolute', left: 16, top: h - 20, color: '#9aa0a6', fontFamily: theme.fonts.sans, fontSize: 14, lineHeight: '16px', zIndex: 96}}>
          {p.credit}
        </div>
      )}
    </>
  );
};

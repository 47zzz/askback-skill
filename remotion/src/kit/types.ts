// render.py 寫出的 work/remotion_props.json 的型別（見 scripts/draw.py 的 scene_props）。
export type Run = {s: string; color: string; mark: string; pos: string; gid: number | null; frac?: {num: Run[]; den: Run[]}};
export type LineItem = {
  beat: number; kind: 'line'; runs: Run[]; size: number; width: number; tall: boolean; advance: number;
  col: number; indent: number; align?: string | null; who: string; bold: boolean;
};
export type ShapeItem = {
  beat: number; kind: 'shape'; shape: string; color: string; fill?: string; width?: number; dash?: boolean;
  at?: number[]; r?: number; ry?: number; from?: number[]; to?: number[]; points?: number[][];
  text?: string; runs?: Run[]; size?: number; plate?: boolean;
};
export type ClearItem = {beat: number; kind: 'clear'};
export type Item = LineItem | ShapeItem | ClearItem;
export type Scene = {
  title: {runs: Run[]; size: number} | null; layout: string; cols: number;
  text_rect: number[] | null; fig_box: number[] | null; image: string | null; crop?: number[] | null;
  base: number; col_w: number; items: Item[];
};
export type Beat = {i: number; start: number; dur: number; hold: number; say: string; sub: string; who: string; scene: number};
export type Style = {
  form: 'board' | 'slides' | 'story' | 'lab'; bg: string; ink: string; font: 'hand' | 'sans'; reveal: 'write' | 'fade' | 'pop';
  size: number; colors: Record<string, string>; title_color: string;
};
export type Props = {
  w: number; h: number; fps: number; total: number; subH: number; badge: string; credit: string;
  style: Style; audio: string; beats: Beat[]; scenes: Scene[];
};
export const EMPTY: Props = {
  w: 1280, h: 720, fps: 30, total: 10, subH: 104, badge: 'AI 生成教學示範，非原講者本人', credit: '',
  style: {form: 'board', bg: '#000', ink: '#fff', font: 'hand', reveal: 'write', size: 50, colors: {w: '#fff'}, title_color: 'y'},
  audio: '', beats: [], scenes: [],
};

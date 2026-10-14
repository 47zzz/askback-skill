/** 和 scripts/draw.py 的 mix() 一樣：兩個 hex 顏色之間取 t。 */
export const mix = (a: string, b: string, t: number): string => {
  const p = (h: string) => {
    const s = h.replace('#', '');
    const v = s.length === 3 ? s.split('').map((c) => c + c).join('') : s;
    return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16));
  };
  const [x, y] = [p(a), p(b)];
  const c = x.map((v, i) => Math.round(v + (y[i] - v) * t));
  return `#${c.map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, '0')).join('')}`;
};

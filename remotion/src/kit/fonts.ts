import {continueRender, delayRender, staticFile} from 'remotion';
import {useEffect, useState} from 'react';

const FONTS: [string, string][] = [
  ['Noto Sans TC', 'fonts/NotoSansTC.ttf'],
  ['Iansui', 'fonts/Iansui-Regular.ttf'],
];

/** 字型載好之前不出圖，才不會有豆腐字。字型檔由 render.py 從 ../fonts 複製到 public/fonts。 */
export const useFonts = () => {
  const [handle] = useState(() => delayRender('fonts'));
  useEffect(() => {
    Promise.all(
      FONTS.map(async ([family, file]) => {
        const face = new FontFace(family, `url(${staticFile(file)})`);
        await face.load();
        document.fonts.add(face);
      }),
    )
      .catch(() => undefined)
      .then(() => continueRender(handle));
  }, [handle]);
};

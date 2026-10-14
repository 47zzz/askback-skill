import React from 'react';
import {CalculateMetadataFunction, Composition} from 'remotion';
import {Lesson} from './Lesson';
import {EMPTY, Props} from './kit/types';

// 長度完全由 render.py 算好的 total（秒）決定：旁白多長，影片就多長。
const metadata: CalculateMetadataFunction<Props> = async ({props}) => ({
  durationInFrames: Math.max(1, Math.ceil(props.total * props.fps)),
  fps: props.fps,
  width: props.w,
  height: props.h,
});

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Lesson"
    component={Lesson}
    width={1280}
    height={720}
    fps={30}
    durationInFrames={300}
    defaultProps={EMPTY}
    calculateMetadata={metadata}
  />
);

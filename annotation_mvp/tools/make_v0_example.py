#!/usr/bin/env python3
"""Create six synthetic JPEG versions and their 15 unordered pairs for UI testing."""
import argparse
import itertools
from pathlib import Path
import sys
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from annotation_mvp.pair_lists import write_pairs


def create_example(out):
    out=Path(out).resolve()
    if out.exists():raise ValueError('示例目录已存在，不覆盖')
    inputs=out/'input';inputs.mkdir(parents=True)
    y,x=np.mgrid[0:360,0:540]
    base=np.stack([.15+.6*x/539,.15+.6*y/359,.25+.3*np.sin(x/50)**2],axis=-1)
    base[(x-170)**2+(y-170)**2<70**2]=[.7,.45,.25]
    base[(x>340)&(x<450)&(y>80)&(y<270)]=[.25,.55,.7]
    names=[]
    for i in range(6):
        # Synthetic stimulus only; this is not an empirical limpidity ordering.
        strength=.55+i*.09
        rgb=np.clip((base-.5)*strength+.5,0,1)
        name=f'version-{i+1}.jpg';Image.fromarray((rgb*255).astype(np.uint8)).save(inputs/name,quality=95);names.append(name)
    rows=[dict(image_a=a,image_b=b,scene_id='demo-scene',pair_id=f'demo-{i+1:02d}',question_id='limpidity')
          for i,(a,b) in enumerate(itertools.combinations(names,2))]
    write_pairs(out/'pairs.csv',rows)
    return out

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    try:out=create_example(args.out)
    except (ValueError,OSError) as e:parser.error(str(e))
    print(f'已生成6张合成示例图、15对清单：{out}；仅供流程验证，不是研究数据。')

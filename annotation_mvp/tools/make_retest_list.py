#!/usr/bin/env python3
"""Sample original pairs from a completed v0 experiment, without changing it."""
import argparse
import math
from pathlib import Path
import random
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from annotation_mvp.pair_protocol import read_source, source_rows
from annotation_mvp.pair_lists import write_pairs


def make_retest(results, fraction, seed, out):
    if not 0 < fraction <= 1:
        raise ValueError('fraction 必须大于0且不超过1')
    out = Path(out).resolve()
    source_root = Path(results).resolve()
    if out == source_root or source_root in out.parents:
        raise ValueError('重测清单不能写入来源实验目录')
    if out.exists():
        raise ValueError('输出清单已存在，不覆盖')
    source = read_source(results)
    originals = source_rows(source)
    rng = random.Random(seed)
    rows = rng.sample(originals,math.ceil(len(originals)*fraction))
    if len(rows) < 2:
        raise ValueError('抽样不足2对，无法生成可导入的独立质控子集；请提高 fraction')
    for row in rows:
        if rng.randrange(2):row['image_a'],row['image_b']=row['image_b'],row['image_a']
    write_pairs(out, rows)
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=Path,required=True)
    parser.add_argument('--fraction',type=float,default=.5)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    try:rows=make_retest(args.results,args.fraction,args.seed,args.out)
    except (ValueError,OSError) as e:parser.error(str(e))
    print(f'已导出 {len(rows)} 对；原始平局保留，所有追加重复呈现剔除。新实验会再次随机左右。')

if __name__=='__main__':main()

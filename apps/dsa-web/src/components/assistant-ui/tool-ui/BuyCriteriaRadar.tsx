import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
} from 'recharts';
import type { BuyCriteriaItem } from '../../../utils/toolResults';

/** 8 维评分雷达。recharts 体积大,单独懒加载,避免进入 thread 主 chunk。 */
function scoreOf(item: BuyCriteriaItem): number {
  return item.passed ? 1 : 0.2;
}

const BuyCriteriaRadar = ({ criteria, isBuy }: { criteria: BuyCriteriaItem[]; isBuy: boolean }) => {
  const data = [...criteria]
    .sort((a, b) => a.index - b.index)
    .map((c) => ({ name: c.criterion_name, score: scoreOf(c), passed: c.passed }));
  const color = isBuy ? '#10b981' : '#ef4444';
  return (
    <ResponsiveContainer width="100%" height="100%">
      <RadarChart data={data} outerRadius="72%">
        <PolarGrid stroke="hsl(var(--border))" />
        <PolarAngleAxis dataKey="name" tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 10 }} />
        <Radar name="判定" dataKey="score" stroke={color} fill={color} fillOpacity={0.25} />
      </RadarChart>
    </ResponsiveContainer>
  );
};

export default BuyCriteriaRadar;

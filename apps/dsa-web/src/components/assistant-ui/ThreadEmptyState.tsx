import type { FC } from 'react';
import { useEffect, useRef, useState } from 'react';
import { ThreadPrimitive } from '@assistant-ui/react';
import {
  ChevronDownIcon,
  DatabaseIcon,
  FileSearchIcon,
  GitCompareArrowsIcon,
  SparklesIcon,
} from 'lucide-react';
import {
  ASSISTANT_SUGGESTION_GROUPS,
  type AssistantSuggestionGroup,
} from '../../utils/assistantQuickActions';
import { cn } from '../../utils/cn';
import { Tooltip } from '../common/Tooltip';

const PRIMARY_SUGGESTION_GROUP_IDS = ['research', 'stock-tools'];

const SuggestionGroup: FC<{ group: AssistantSuggestionGroup }> = ({ group }) => (
  <section aria-labelledby={`suggestion-group-${group.id}`}>
    <div className="mb-1.5 flex min-w-0 items-baseline gap-2 px-1">
      <h3 id={`suggestion-group-${group.id}`} className="shrink-0 text-[11px] font-semibold text-foreground">
        {group.title}
      </h3>
      <span className="min-w-0 truncate text-[10px] text-muted-foreground">{group.description}</span>
    </div>
    <div className="grid gap-1.5 min-[520px]:grid-cols-2">
      {group.items.map((suggestion) => (
        <SuggestionCard
          key={suggestion.label}
          label={suggestion.label}
          prompt={suggestion.prompt}
        />
      ))}
    </div>
  </section>
);

const SuggestionCard: FC<{ label: string; prompt: string }> = ({ label, prompt }) => {
  const cardRef = useRef<HTMLSpanElement | null>(null);
  const [isTruncated, setIsTruncated] = useState(false);

  useEffect(() => {
    const button = cardRef.current?.querySelector('button');
    if (!button) return;

    const updateTruncation = () => {
      setIsTruncated(button.scrollWidth > button.clientWidth);
    };
    updateTruncation();

    const observer = new ResizeObserver(updateTruncation);
    observer.observe(button);
    return () => observer.disconnect();
  }, [isTruncated, label]);

  const card = (
    <span ref={cardRef} className="block min-w-0 w-full">
      <ThreadPrimitive.Suggestion
        className={cn(
          'block w-full min-w-0 !truncate rounded-xl border border-border bg-card px-3 py-1.5 text-left',
          '!text-[11px] !leading-4 text-foreground/85 shadow-sm transition',
          'hover:-translate-y-0.5 hover:border-primary/30 hover:bg-primary/5 hover:text-foreground hover:shadow-md',
        )}
        prompt={prompt}
        clearComposer
      >
        {label}
      </ThreadPrimitive.Suggestion>
    </span>
  );

  if (!isTruncated) return card;

  return (
    <Tooltip content={label} className="min-w-0 w-full" contentClassName="min-w-0 whitespace-normal">
      {card}
    </Tooltip>
  );
};

const CAPABILITIES = [
  { title: '多源数据核验', description: '行情、财务、公告、研报与新闻按问题自动组合。', prompt: '请核验下面这条信息，结合行情、财务、公告、研报与新闻给出来源和证据缺口：', icon: DatabaseIcon },
  { title: '产业链研究', description: '拆解受益环节、兑现路径、催化与主要反证。', prompt: '请研究下面这个行业或产业链，拆解关键环节、受益公司、兑现路径、催化与主要反证：', icon: FileSearchIcon },
  { title: '连续比较追问', description: '沿用本次研究上下文继续映射公司和比较标的。', prompt: '请基于当前研究上下文继续比较下面这些公司或标的，保持口径一致并说明差异：', icon: GitCompareArrowsIcon },
];

export const EmptyState: FC = () => {
  const [showMoreSuggestions, setShowMoreSuggestions] = useState(false);
  const primaryGroups = ASSISTANT_SUGGESTION_GROUPS.filter((group) => PRIMARY_SUGGESTION_GROUP_IDS.includes(group.id));
  const secondaryGroups = ASSISTANT_SUGGESTION_GROUPS.filter((group) => !PRIMARY_SUGGESTION_GROUP_IDS.includes(group.id));

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-4xl flex-1 flex-col items-center overflow-x-hidden overflow-y-auto px-2 pb-1 pt-1 text-center [scrollbar-gutter:stable] [scrollbar-width:thin] sm:pt-4">
      <div className="hidden size-12 items-center justify-center rounded-2xl border border-primary/20 bg-card text-primary shadow-[0_18px_50px_hsl(var(--primary)/0.14)] sm:flex">
        <SparklesIcon className="size-6" />
      </div>
      <div className="mt-1 sm:mt-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-primary sm:text-xs sm:tracking-[0.18em]">A-SHARE RESEARCH AGENT</p>
        <h2 className="mt-1 text-xl font-semibold tracking-tight text-foreground sm:mt-2 sm:text-3xl">把问题交给会查数据的投研助手</h2>
        <p className="mx-auto mt-1 hidden max-w-2xl text-sm leading-6 text-muted-foreground sm:block">
          支持产业链研究、公司比较、财务与估值核验、行情和事件追踪。回答会保留数据时间、来源与风险边界，并能沿着上一轮继续追问。
        </p>
      </div>

      <div className="mt-3 grid w-full grid-cols-3 gap-1.5 text-left sm:mt-5 sm:gap-3">
        {CAPABILITIES.map(({ title, description, icon: Icon, prompt }) => (
          <ThreadPrimitive.Suggestion
            key={title}
            prompt={prompt}
            clearComposer
            className="group block w-full rounded-lg border border-border/70 bg-card/70 px-2 py-2 text-left transition hover:-translate-y-0.5 hover:border-primary/30 hover:bg-primary/5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25 sm:rounded-2xl sm:border-border/80 sm:bg-card/80 sm:p-3.5 sm:shadow-sm"
          >
            <div className="flex min-w-0 flex-col items-center gap-1 text-center sm:flex-row sm:gap-2.5 sm:text-left">
              <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary transition group-hover:bg-primary/15 sm:size-8 sm:rounded-xl">
                <Icon className="size-3.5 sm:size-4" />
              </div>
              <p className="min-w-0 text-center text-[11px] font-semibold leading-4 text-foreground sm:text-left sm:text-sm">{title}</p>
            </div>
            <p className="mt-2 hidden text-xs leading-5 text-muted-foreground sm:block">{description}</p>
          </ThreadPrimitive.Suggestion>
        ))}
      </div>

      <div className="mt-3 flex min-h-0 w-full flex-1 flex-col text-left sm:mt-5">
        <div className="mb-2 flex min-w-0 items-baseline justify-between gap-2 px-1">
          <p className="shrink-0 text-xs font-medium text-muted-foreground">你可以这样问</p>
          <span className="min-w-0 truncate text-[10px] text-muted-foreground">点击后补充标的、行业或资料对象</span>
        </div>
        <div className="w-full flex-none space-y-3 pr-2">
          {primaryGroups.map((group) => <SuggestionGroup key={group.id} group={group} />)}

          {secondaryGroups.length > 0 ? (
            <>
              <button
                type="button"
                className="flex w-full items-center justify-between rounded-lg border border-dashed border-border/80 bg-card/40 px-3 py-2 text-left text-[11px] font-medium text-muted-foreground transition hover:border-primary/30 hover:bg-primary/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
                onClick={() => setShowMoreSuggestions((visible) => !visible)}
                aria-expanded={showMoreSuggestions}
              >
                <span>{showMoreSuggestions ? '收起其他问题' : `更多问题（${secondaryGroups.reduce((count, group) => count + group.items.length, 0)}）`}</span>
                <ChevronDownIcon className={cn('size-3.5 transition-transform', showMoreSuggestions && 'rotate-180')} />
              </button>
              {showMoreSuggestions ? secondaryGroups.map((group) => <SuggestionGroup key={group.id} group={group} />) : null}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
};

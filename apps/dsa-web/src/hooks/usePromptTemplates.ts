import { useEffect, useState } from 'react';
import { promptsApi, type PromptTemplateItem } from '../api/prompts';

export interface UsePromptTemplatesResult {
  templates: PromptTemplateItem[];
  selectedTemplateId: string;
  setSelectedTemplateId: (id: string) => void;
  setTemplates: (templates: PromptTemplateItem[]) => void;
  selectedTemplate: PromptTemplateItem | undefined;
}

export function usePromptTemplates(): UsePromptTemplatesResult {
  const [templates, setTemplates] = useState<PromptTemplateItem[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState('');

  useEffect(() => {
    let active = true;
    promptsApi.getPromptTemplates().then((items) => {
      if (!active) return;
      setTemplates(items);
      if (items.length > 0 && !selectedTemplateId) {
        const defaultTemplate = items.find((t) => t.is_default);
        setSelectedTemplateId(defaultTemplate?.id || items[0]?.id || '');
      }
    }).catch(() => {});
    return () => { active = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedTemplate = templates.find((template) => template.id === selectedTemplateId);

  return {
    templates,
    selectedTemplateId,
    setSelectedTemplateId,
    setTemplates,
    selectedTemplate,
  };
}

export default usePromptTemplates;

import React from 'react';

export function renderReportParagraphs(text: string): React.ReactNode {
  const normalized = text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  const paragraphs = normalized.length > 0
    ? normalized
    : text
      .split('\n')
      .map((paragraph) => paragraph.trim())
      .filter(Boolean);

  if (paragraphs.length === 0) {
    return null;
  }

  return (
    <div className="space-y-5">
      {paragraphs.map((paragraph, index) => (
        <p key={`${index}-${paragraph.slice(0, 16)}`} className="market-stream-paragraph">
          {paragraph}
        </p>
      ))}
    </div>
  );
}
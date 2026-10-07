import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, ExternalLink, ZoomIn, ZoomOut } from 'lucide-react';
import * as pdfjsLib from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { Button } from '../common';

// Worker 配置只执行一次：`?url` 让 vite 把 worker 输出为独立 asset，懒加载时才下载。
pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

interface PdfViewerProps {
  /** Same-origin, authenticated TextDocumentResource URL. */
  resourceUrl: string;
  /** Optional page to show after the PDF is loaded. */
  initialPage?: number;
}

type Status = 'loading' | 'ready' | 'error';

/**
 * PDF.js canvas renderer, lazy-loaded so pdfjs-dist (~300KB) stays out of the
 * main chunk. Renders one page at a time into a single reused canvas with a
 * toolbar (prev/next/zoom/open-in-new-tab). Used in the RSS item detail drawer
 * for feeds whose items are PDF reports (e.g. nanhua 研报).
 *
 * The "open in new tab" link uses the same proxy URL — the backend returns
 * `Content-Disposition: inline`, so the browser's built-in PDF viewer renders
 * it as a fallback if PDF.js fails.
 */
const PdfViewer: React.FC<PdfViewerProps> = ({ resourceUrl, initialPage = 1 }) => {
  const [status, setStatus] = useState<Status>('loading');
  const [errorMsg, setErrorMsg] = useState('');
  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [scale, setScale] = useState(1.2);
  const [rendering, setRendering] = useState(false);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pdfDocRef = useRef<pdfjsLib.PDFDocumentProxy | null>(null);
  const renderTaskRef = useRef<pdfjsLib.RenderTask | null>(null);
  const loadingTaskRef = useRef<pdfjsLib.PDFDocumentLoadingTask | null>(null);
  const currentPageRef = useRef(1);
  const scaleRef = useRef(scale);

  useEffect(() => { currentPageRef.current = currentPage; }, [currentPage]);
  useEffect(() => { scaleRef.current = scale; }, [scale]);

  const renderPage = useCallback(async (pageNum: number) => {
    const doc = pdfDocRef.current;
    const canvas = canvasRef.current;
    if (!doc || !canvas) return;
    // Cancel any in-flight render (page flip / zoom race).
    if (renderTaskRef.current) {
      renderTaskRef.current.cancel();
      renderTaskRef.current = null;
    }
    setRendering(true);
    try {
      const page = await doc.getPage(pageNum);
      const container = containerRef.current;
      // Fit width to the container, but never below 0.6× for readability.
      const baseViewport = page.getViewport({ scale: 1 });
      const containerWidth = container?.clientWidth || baseViewport.width;
      const fitScale = Math.max(0.6, containerWidth / baseViewport.width);
      const effectiveScale = fitScale * scaleRef.current;
      const viewport = page.getViewport({ scale: effectiveScale });

      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;

      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const task = page.render({ canvasContext: ctx, viewport });
      renderTaskRef.current = task;
      await task.promise;
      setStatus('ready');
    } catch (err: unknown) {
      // RenderingCancelledException is expected on rapid page flips / zoom.
      const name = (err as { name?: string })?.name;
      if (name === 'RenderingCancelledException') return;
      setStatus('error');
      setErrorMsg((err as Error)?.message || 'PDF 渲染失败');
    } finally {
      setRendering(false);
    }
  }, []);

  // Load the document when the proxy URL changes.
  useEffect(() => {
    let cancelled = false;
    const requestedPage = Number.isFinite(initialPage) ? Math.max(1, Math.floor(initialPage)) : 1;
    setStatus('loading');
    setErrorMsg('');
    setNumPages(0);
    setCurrentPage(requestedPage);
    currentPageRef.current = requestedPage;

    const loadingTask = pdfjsLib.getDocument({ url: resourceUrl });
    loadingTaskRef.current = loadingTask;
    loadingTask.promise
      .then((doc) => {
        if (cancelled) {
          void doc.destroy();
          return;
        }
        pdfDocRef.current = doc;
        setNumPages(doc.numPages);
        const pageToRender = Math.min(requestedPage, Math.max(1, doc.numPages));
        setCurrentPage(pageToRender);
        void renderPage(pageToRender);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus('error');
        const msg = (err as { status?: number; message?: string });
        setErrorMsg(
          msg.status
            ? `PDF 加载失败（HTTP ${msg.status}）`
            : msg.message || 'PDF 加载失败',
        );
      });

    return () => {
      cancelled = true;
      if (renderTaskRef.current) {
        renderTaskRef.current.cancel();
        renderTaskRef.current = null;
      }
      const doc = pdfDocRef.current;
      pdfDocRef.current = null;
      if (doc) void doc.destroy();
      const task = loadingTaskRef.current;
      loadingTaskRef.current = null;
      if (task) void task.destroy();
    };
  }, [resourceUrl, initialPage, renderPage]);

  // Re-render when the page or zoom changes.
  useEffect(() => {
    if (pdfDocRef.current && numPages > 0) {
      void renderPage(currentPage);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPage, scale]);

  const goPrev = () => currentPage > 1 && setCurrentPage((p) => p - 1);
  const goNext = () => currentPage < numPages && setCurrentPage((p) => p + 1);
  const zoomIn = () => setScale((s) => Math.min(3, +(s + 0.2).toFixed(1)));
  const zoomOut = () => setScale((s) => Math.max(0.4, +(s - 0.2).toFixed(1)));

  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-10 mb-3 flex flex-wrap items-center justify-center gap-1.5 rounded-xl border border-border bg-card/95 px-3 py-2 text-xs backdrop-blur">
        <Button variant="ghost" size="sm" onClick={goPrev} disabled={currentPage <= 1 || rendering}>
          <ChevronLeft className="h-3.5 w-3.5" />
        </Button>
        <span className="min-w-[70px] text-center text-secondary-text">
          {numPages ? `${currentPage} / ${numPages}` : '—'}
        </span>
        <Button variant="ghost" size="sm" onClick={goNext} disabled={currentPage >= numPages || rendering}>
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>
        <span className="mx-1 h-4 w-px bg-border" />
        <Button variant="ghost" size="sm" onClick={zoomOut} disabled={rendering}>
          <ZoomOut className="h-3.5 w-3.5" />
        </Button>
        <span className="min-w-[34px] text-center text-secondary-text">{scale.toFixed(1)}x</span>
        <Button variant="ghost" size="sm" onClick={zoomIn} disabled={rendering}>
          <ZoomIn className="h-3.5 w-3.5" />
        </Button>
        <span className="mx-1 h-4 w-px bg-border" />
        <a
          href={resourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-cyan hover:bg-muted/60"
          title="在新窗口打开（浏览器内置 PDF 查看器）"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          新窗口
        </a>
      </div>

      {status === 'loading' && (
        <div className="flex h-40 items-center justify-center text-sm text-muted-text">
          正在加载 PDF…
        </div>
      )}
      {status === 'error' && (
        <div className="rounded-xl border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          <p>PDF 加载失败：{errorMsg}</p>
          <a href={resourceUrl} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-xs text-cyan hover:underline">
            尝试在新窗口打开
          </a>
        </div>
      )}
      <div ref={containerRef} className="flex justify-center overflow-x-auto">
        {/* Canvas is hidden until first successful render to avoid a blank oversized box. */}
        <canvas
          ref={canvasRef}
          className={status === 'ready' ? 'rounded-lg shadow-sm' : 'hidden'}
        />
      </div>
    </div>
  );
};

export default PdfViewer;

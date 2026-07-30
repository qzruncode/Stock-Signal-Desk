import { describe, expect, it } from 'vitest';

import { isStreamAbortError, readThrownStreamErrorMessage } from '../chatStreamError';


describe('readThrownStreamErrorMessage', () => {
  it('extracts the backend message from a data-stream status error', () => {
    const error = new Error(
      'Status 413: {"error":"message_too_large","message":"请求历史过大，请新建会话后重试"}',
    );

    expect(readThrownStreamErrorMessage(error)).toBe('请求历史过大，请新建会话后重试');
  });

  it('keeps a plain adapter error readable', () => {
    expect(readThrownStreamErrorMessage(new Error('network failed'))).toBe('network failed');
  });
});

describe('isStreamAbortError', () => {
  it('recognizes an explicit abort', () => {
    expect(isStreamAbortError(new DOMException('aborted', 'AbortError'))).toBe(true);
  });

  it('does not mistake a broken response stream for a user cancellation', () => {
    expect(isStreamAbortError(new TypeError('Failed to execute enqueue on ReadableStream'))).toBe(false);
  });
});

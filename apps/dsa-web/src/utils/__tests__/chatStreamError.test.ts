import { describe, expect, it } from 'vitest';

import { readThrownStreamErrorMessage } from '../chatStreamError';


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

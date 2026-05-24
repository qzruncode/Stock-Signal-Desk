import apiClient from './index';

export interface WatchlistResponse {
  codes: string[];
  count: number;
  configVersion: string;
}

export interface WatchlistAddResponse {
  codes: string[];
  count: number;
  added: string[];
  message: string;
  configVersion: string;
}

export interface WatchlistRemoveResponse {
  codes: string[];
  count: number;
  removed: string[];
  message: string;
  configVersion: string;
}

export const watchlistApi = {
  async get(): Promise<WatchlistResponse> {
    const response = await apiClient.get<WatchlistResponse>('/api/v1/watchlist');
    return response.data;
  },

  async add(codes: string[]): Promise<WatchlistAddResponse> {
    const response = await apiClient.post<WatchlistAddResponse>('/api/v1/watchlist/add', codes);
    return response.data;
  },

  async remove(codes: string[]): Promise<WatchlistRemoveResponse> {
    const response = await apiClient.post<WatchlistRemoveResponse>('/api/v1/watchlist/remove', codes);
    return response.data;
  },
};

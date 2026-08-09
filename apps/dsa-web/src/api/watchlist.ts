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

export interface WatchlistGroup {
  id: string;
  name: string;
  codes: string[];
  source?: string;
  sortOrder?: number;
}

export const watchlistApi = {
  async get(signal?: AbortSignal): Promise<WatchlistResponse> {
    const response = await apiClient.get<WatchlistResponse>('/api/v1/watchlist', { signal });
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

  async listGroups(): Promise<WatchlistGroup[]> {
    const response = await apiClient.get<{ groups: WatchlistGroup[] }>('/api/v1/watchlist/groups');
    return response.data.groups || [];
  },

  async createGroup(name: string, codes: string[] = []): Promise<WatchlistGroup> {
    const response = await apiClient.post<WatchlistGroup>('/api/v1/watchlist/groups', {
      name,
      codes,
      source: 'manual',
    });
    return response.data;
  },

  async updateGroup(id: string, patch: { name?: string; codes?: string[] }): Promise<WatchlistGroup> {
    const response = await apiClient.patch<WatchlistGroup>(`/api/v1/watchlist/groups/${id}`, patch);
    return response.data;
  },

  async deleteGroup(id: string): Promise<void> {
    await apiClient.delete(`/api/v1/watchlist/groups/${id}`);
  },
};

/**
 * Stock Index Type Definitions
 *
 * Stock data index for autocomplete functionality
 */

type Market = 'CN' | 'HK' | 'US' | 'INDEX' | 'ETF' | 'BSE';
type AssetType = 'stock' | 'index' | 'etf';

/**
 * Stock index item (full format)
 */
export interface StockIndexItem {
  /** Canonical code: 600519.SH */
  canonicalCode: string;
  /** Display code: 600519 */
  displayCode: string;
  /** Chinese name: 贵州茅台 */
  nameZh: string;
  /** English name: Kweichow Moutai */
  nameEn?: string;
  /** Pinyin full: guizhoumaotai */
  pinyinFull?: string;
  /** Pinyin abbreviation: gzmt */
  pinyinAbbr?: string;
  /** Aliases: ["茅台"] */
  aliases?: string[];
  /** Market */
  market: Market;
  /** Asset type */
  assetType: AssetType;
  /** Is active */
  active: boolean;
  /** Popularity */
  popularity?: number;
}

/**
 * Compressed format stock index item (for reducing file size)
 */
export type StockIndexTuple = [
  string,  // canonicalCode
  string,  // displayCode
  string,  // nameZh
  string | undefined, // pinyinFull
  string | undefined, // pinyinAbbr
  string[], // aliases (required, use empty array if none)
  Market,
  AssetType,
  boolean, // active
  number | undefined, // popularity
];

/**
 * Stock index data (supports two formats)
 */
export type StockIndexData = StockIndexItem[] | StockIndexTuple[];

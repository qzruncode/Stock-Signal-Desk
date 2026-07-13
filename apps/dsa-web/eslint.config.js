import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'playwright-report', 'test-results']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // react-hooks v7 把 set-state-in-effect 设为 error，但它对 "effect 里调用
      // 会 setState 的数据加载函数"（useEffect(() => { void fetchX() }, [fetchX])
      // 这种 React 官方推荐的数据加载模式）也报。项目现有数据层大量使用此模式，
      // 强行重构会引入竞态/abort 回归，故降为 warn 留待后续按 react-query/use(promise)
      // 方案统一迁移时再收紧。
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
])

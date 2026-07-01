import * as assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const routerSource = readFileSync(resolve(__dirname, '../src/router/index.ts'), 'utf8')
const menuItemSource = readFileSync(resolve(__dirname, '../src/components/layout/MenuItem.vue'), 'utf8')

const semanticRoute =
  routerSource.match(/path:\s*'\/set\/semantic'[\s\S]*?meta:\s*\{[\s\S]*?\}/)?.[0] || ''

assert.match(semanticRoute, /title:\s*'语义资产'/)
assert.match(semanticRoute, /iconActive:\s*'model'/)
assert.match(semanticRoute, /iconDeActive:\s*'noModel'/)
assert.match(semanticRoute, /hiddenInSubMenu:\s*true/)
assert.match(menuItemSource, /children\s*\.filter\(\(ele: any\) => !ele\.meta\?\.hiddenInSubMenu\)/)

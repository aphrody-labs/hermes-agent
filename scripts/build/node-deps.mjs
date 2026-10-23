#!/usr/bin/env bun
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'

import { prepareElectronRuntime } from './electron-runtime.mjs'

// Bun installs the workspaces and runs every build script, so this script runs on Bun itself.
export function bunCommand({ env = process.env } = {}) {
  if (!process.versions.bun) throw new Error('scripts/build/node-deps.mjs runs on Bun')
  return env.HERMES_BUN || process.execPath
}

function workspacePaths(source, manifest) {
  const globs = Array.isArray(manifest.workspaces) ? manifest.workspaces : manifest.workspaces?.packages ?? []
  const paths = new Set()
  for (const pattern of globs) {
    for (const match of new Bun.Glob(`${pattern}/package.json`).scanSync({ cwd: source, onlyFiles: true })) {
      paths.add(dirname(match).replaceAll('\\', '/'))
    }
  }
  return [...paths]
}

// Each direct dependency of the root and of the selected workspaces resolves to a package
// manifest, as Bun's module resolution finds it. A partial cleanup or snapshot restore can
// leave gutted directories behind, and product builders resolve tools through these manifests.
function dependenciesResolve(source, directories) {
  for (const directory of directories) {
    const manifest = JSON.parse(readFileSync(join(source, directory, 'package.json'), 'utf8'))
    for (const name of Object.keys({ ...manifest.dependencies, ...manifest.devDependencies })) {
      let current = join(source, directory)
      while (!existsSync(join(current, 'node_modules', name, 'package.json'))) {
        const parent = dirname(current)
        if (parent === current) return false
        current = parent
      }
    }
  }
  return true
}

/** Install the full requested workspace union in one strict, locked operation. */
export function prepareNodeDependencies({ source, workspaces, env = process.env, reuse = false, install = true, nativeToolchain }) {
  source = resolve(source)
  if (!Array.isArray(workspaces) || workspaces.length === 0) {
    throw new Error('Select at least one workspace; implicit all-workspace installation is not allowed')
  }
  const manifest = JSON.parse(readFileSync(join(source, 'package.json'), 'utf8'))
  const known = workspacePaths(source, manifest)
  const selected = [...new Set(workspaces.map(workspace => {
    const path = known.find(path => path === workspace
      || JSON.parse(readFileSync(join(source, path, 'package.json'), 'utf8')).name === workspace)
    if (!path) throw new Error(`Unknown or missing workspace: ${workspace}`)
    return path
  }))].sort()
  const prepareElectron = selected.includes('apps/desktop')
    && Boolean(JSON.parse(readFileSync(join(source, 'apps/desktop/package.json'), 'utf8')).devDependencies?.electron)
  const bun = bunCommand({ env })
  const range = manifest.engines?.bun
  if (range && !Bun.semver.satisfies(Bun.version, range)) throw new Error(`bun ${Bun.version} violates ${range}`)
  const args = ['install', '--frozen-lockfile', ...selected.flatMap(workspace => ['--filter', `./${workspace}`])]
  // This receipt certifies dependency preparation, never compiled product freshness.
  // Keep it inside the cached tree so a clean install also removes the receipt.
  const receipt = join(source, 'node_modules/.hermes-node-deps')
  // Ordinary product builders consume the baseline receipt; preparation also binds
  // lifecycle outputs to its compiler/SDK identity.
  const nativeReceipt = `${receipt}.native-toolchain`
  const nativeKey = nativeToolchain === undefined ? undefined : JSON.stringify(nativeToolchain)
  const inputs = createHash('sha256').update(JSON.stringify({
    bun: Bun.version, platform: process.platform, arch: process.arch, args,
  }))
  const files = ['bun.lock', 'bunfig.toml', 'package.json', ...known.map(path => `${path}/package.json`)].sort()
  for (const file of files) {
    inputs.update(file).update('\0').update(existsSync(join(source, file)) ? readFileSync(join(source, file)) : '<missing>').update('\0')
  }
  const key = `${inputs.digest('hex')}\n`
  const completed = () => existsSync(receipt) && readFileSync(receipt, 'utf8') === key
    && (nativeKey === undefined || (existsSync(nativeReceipt) && readFileSync(nativeReceipt, 'utf8') === `${key}${nativeKey}\n`))
    && dependenciesResolve(source, ['.', ...selected])
  if (reuse && completed()) {
    if (prepareElectron) prepareElectronRuntime({ source, install, env })
    console.log(`node-deps: reusing completed install (${selected.join(', ')})`)
    return { source, workspaces: selected }
  }
  if (!install) throw new Error('Workspace dependencies are stale or missing and lazy installs are disabled; run an explicit build/update')
  // A failed install must not leave a receipt claiming the previous tree. Invalidate first.
  rmSync(receipt, { force: true })
  rmSync(nativeReceipt, { force: true })
  console.log(`node-deps: installing workspace dependencies with bun install (${selected.join(', ')})...`)
  execFileSync(bun, args, { cwd: source, env, stdio: 'inherit' })
  if (prepareElectron) prepareElectronRuntime({ source, install, env })
  if (reuse) {
    writeFileSync(receipt, key)
    if (nativeKey !== undefined) writeFileSync(nativeReceipt, `${key}${nativeKey}\n`)
  }
  return { source, workspaces: selected }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  // The Python bundle driver runs Bun's package manager through this same resolver.
  if (process.argv[2] === '--bun') {
    const child = spawnSync(bunCommand(), process.argv.slice(3), { stdio: 'inherit' })
    if (child.error) throw child.error
    process.exit(child.status ?? 1)
  }
  const { values } = parseArgs({ options: {
    source: { type: 'string' }, workspace: { type: 'string', multiple: true },
    reuse: { type: 'boolean', default: false },
    'no-install': { type: 'boolean', default: false },
    'native-toolchain': { type: 'string' },
  } })
  if (!values.source) throw new Error('--source is required')
  prepareNodeDependencies({ source: values.source, workspaces: values.workspace, reuse: values.reuse, install: !values['no-install'], nativeToolchain: values['native-toolchain'] })
}

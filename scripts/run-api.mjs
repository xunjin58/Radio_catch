import { existsSync } from 'node:fs'
import { spawn } from 'node:child_process'
import { resolve } from 'node:path'
import process from 'node:process'

const root = process.cwd()
const backend = resolve(root, 'backend')
const python = resolve(
  backend,
  process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python',
)

if (!existsSync(python)) {
  console.error(`未找到后端虚拟环境：${python}`)
  console.error('请先按 README 创建 backend/.venv 并安装 backend/requirements.txt。')
  process.exitCode = 1
} else {
  const child = spawn(python, ['run.py'], { cwd: backend, stdio: 'inherit' })
  child.on('error', (error) => {
    console.error(`无法启动 API：${error.message}`)
    process.exitCode = 1
  })
  child.on('exit', (code, signal) => {
    if (signal) process.exitCode = 1
    else if (code !== null) process.exitCode = code
  })
}

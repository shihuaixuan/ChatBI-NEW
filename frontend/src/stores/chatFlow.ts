import { defineStore } from 'pinia'
import { store } from '@/stores/index.ts'

export type ChatFlowMode = 'graph' | 'agent'

const STORAGE_KEY = 'numora_chat_flow_mode'
const PREVIOUS_STORAGE_KEY = 'sqlbot_chat_flow_mode'
const AVAILABLE_MODES: ChatFlowMode[] = ['graph', 'agent']

const isAvailableMode = (value: string | null): value is ChatFlowMode =>
  value !== null && AVAILABLE_MODES.includes(value as ChatFlowMode)

const restoreMode = (): ChatFlowMode => {
  const saved = localStorage.getItem(STORAGE_KEY) as ChatFlowMode | null
  if (isAvailableMode(saved)) {
    return saved
  }

  // 仅迁移旧存储中仍然有效的 graph/agent，其他值直接废弃。
  const previousSaved = localStorage.getItem(PREVIOUS_STORAGE_KEY)
  localStorage.removeItem(PREVIOUS_STORAGE_KEY)
  if (isAvailableMode(previousSaved)) {
    localStorage.setItem(STORAGE_KEY, previousSaved)
    return previousSaved
  }
  return 'graph'
}

interface ChatFlowState {
  mode: ChatFlowMode
}

export const chatFlowStore = defineStore('chatFlowStore', {
  state: (): ChatFlowState => {
    return {
      mode: restoreMode(),
    }
  },
  getters: {
    // 新消息实际走的链路。
    getMode(): ChatFlowMode {
      return this.mode
    },
    getAvailableModes(): ChatFlowMode[] {
      return AVAILABLE_MODES
    },
    // 选择器可由部署配置隐藏，隐藏时固定使用已保存模式或 graph。
    getSelectorEnabled(): boolean {
      return import.meta.env.VITE_CHATBI_FLOW_SELECTOR_ENABLED === 'true'
    },
  },
  actions: {
    setMode(mode: ChatFlowMode) {
      if (!AVAILABLE_MODES.includes(mode)) {
        return
      }
      this.mode = mode
      localStorage.setItem(STORAGE_KEY, mode)
    },
  },
})

export const useChatFlowStore = () => {
  return chatFlowStore(store)
}

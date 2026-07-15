import { defineStore } from 'pinia'
import { store } from '@/stores/index.ts'

export type ChatFlowMode = 'graph' | 'agent' | 'agentic' | 'legacy'

const STORAGE_KEY = 'sqlbot_chat_flow_mode'

// 各链路是否可用仍由构建时开关决定，选择器只在可用链路中切换。
const enabledModes = (): ChatFlowMode[] => {
  const modes: ChatFlowMode[] = []
  if (import.meta.env.VITE_GRAPH_CHATBI_ENABLED === 'true') modes.push('graph')
  if (import.meta.env.VITE_AGENT_CHATBI_ENABLED === 'true') modes.push('agent')
  if (import.meta.env.VITE_AGENTIC_CHATBI_ENABLED === 'true') modes.push('agentic')
  modes.push('legacy')
  return modes
}

// 与原先写死的优先级一致：graph > agent > agentic > legacy。
const defaultMode = (): ChatFlowMode => enabledModes()[0]

const restoreMode = (): ChatFlowMode => {
  const saved = localStorage.getItem(STORAGE_KEY) as ChatFlowMode | null
  if (saved && enabledModes().includes(saved)) {
    return saved
  }
  return defaultMode()
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
    // 无 execution_type 的旧记录按原有优先级渲染，不随选择器切换。
    getDefaultMode(): ChatFlowMode {
      return defaultMode()
    },
    getAvailableModes(): ChatFlowMode[] {
      return enabledModes()
    },
    // 选择器需显式开启（生产默认关闭，保持原有固定链路行为）。
    getSelectorEnabled(): boolean {
      return (
        import.meta.env.VITE_CHATBI_FLOW_SELECTOR_ENABLED === 'true' && enabledModes().length > 1
      )
    },
  },
  actions: {
    setMode(mode: ChatFlowMode) {
      if (!enabledModes().includes(mode)) {
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

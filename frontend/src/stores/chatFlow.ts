import { defineStore } from 'pinia'
import { store } from '@/stores/index.ts'

export type ChatFlowMode = 'agent'

const STORAGE_KEY = 'numora_chat_flow_mode'
const PREVIOUS_STORAGE_KEY = 'sqlbot_chat_flow_mode'
const AGENT_MODE: ChatFlowMode = 'agent'
const AVAILABLE_MODES: ChatFlowMode[] = [AGENT_MODE]

const restoreMode = (): ChatFlowMode => {
  // 新问题统一走 Agent，旧版本保存的 Graph 选择不再参与请求路由。
  localStorage.removeItem(PREVIOUS_STORAGE_KEY)
  localStorage.setItem(STORAGE_KEY, AGENT_MODE)
  return AGENT_MODE
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
    // Agent 单链路下不再向用户展示执行模式选择器。
    getSelectorEnabled(): boolean {
      return false
    },
  },
  actions: {
    setMode(mode: ChatFlowMode) {
      if (mode !== AGENT_MODE) {
        return
      }
      this.mode = AGENT_MODE
      localStorage.setItem(STORAGE_KEY, AGENT_MODE)
    },
  },
})

export const useChatFlowStore = () => {
  return chatFlowStore(store)
}

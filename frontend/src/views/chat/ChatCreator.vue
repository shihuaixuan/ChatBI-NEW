<script lang="ts" setup>
import { onMounted, ref, computed, shallowRef, onBeforeUnmount, onBeforeMount } from 'vue'
import icon_close_outlined from '@/assets/svg/operate/ope-close.svg'
import EmptyBackground from '@/views/dashboard/common/EmptyBackground.vue'
import icon_searchOutline_outlined from '@/assets/svg/icon_search-outline_outlined.svg'
import { chatApi, ChatInfo } from '@/api/chat.ts'
import { headlessApi } from '@/api/headless'
import Card from '@/views/ds/ChatCard.vue'
import { useAssistantStore } from '@/stores/assistant'
const assistantStore = useAssistantStore()

const props = withDefaults(
  defineProps<{
    hidden?: boolean
  }>(),
  {
    hidden: false,
  }
)

const searchLoading = ref(false)
const datasetConfigVisible = ref(false)
const keywords = ref('')
const datasetList = shallowRef([] as any[])
const datasetListWithSearch = computed(() => {
  if (!keywords.value) return datasetList.value
  return datasetList.value.filter((ele) =>
    ele.name.toLowerCase().includes(keywords.value.toLowerCase())
  )
})
const beforeClose = () => {
  datasetConfigVisible.value = false
  keywords.value = ''
}

const emits = defineEmits(['onChatCreated'])

function listDatasets() {
  searchLoading.value = true
  headlessApi
    .datasetList()
    .then((res) => {
      datasetList.value = Array.isArray(res) ? res : []
    })
    .finally(() => {
      searchLoading.value = false
    })
}

const innerDataset = ref()

const loading = ref(false)

function showDs() {
  listDatasets()
  datasetConfigVisible.value = true
}

function hideDs() {
  innerDataset.value = undefined
  datasetConfigVisible.value = false
}

function selectDatasetInDialog(dataset: any) {
  innerDataset.value = dataset.id
}

function confirmSelectDataset() {
  if (innerDataset.value) {
    createChat(innerDataset.value)
  }
}

function createChat(datasetId: number) {
  loading.value = true
  const param = {
    dataset_id: datasetId,
  } as any
  let method = chatApi.startChat
  if (assistantStore.getAssistant) {
    param['origin'] = 2
    method = chatApi.startAssistantChat
  }
  method(param)
    .then((res) => {
      const chat: ChatInfo | undefined = chatApi.toChatInfo(res)
      if (chat == undefined) {
        throw Error('chat is undefined')
      }
      emits('onChatCreated', chat)
      hideDs()
    })
    .catch((e) => {
      console.error(e)
    })
    .finally(() => {
      loading.value = false
    })
}

const drawerHeight = ref(0)
const setHeight = () => {
  drawerHeight.value = document.body.clientHeight - 100
  console.log(drawerHeight.value)
}

onMounted(() => {
  if (props.hidden) {
    return
  }
})

onBeforeMount(() => {
  setHeight()
  window.addEventListener('resize', setHeight)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', setHeight)
})

defineExpose({
  showDs,
  hideDs,
  createChat,
})
</script>

<template>
  <div v-loading.body.fullscreen.lock="loading">
    <el-drawer
      v-model="datasetConfigVisible"
      :close-on-click-modal="false"
      :size="drawerHeight"
      modal-class="datasource-drawer-chat"
      direction="btt"
      :before-close="beforeClose"
      :show-close="false"
    >
      <template #header="{ close }">
        <span style="white-space: nowrap">选择数据集</span>
        <div class="flex-center" style="width: 100%; margin-right: 32px">
          <el-input
            v-model="keywords"
            clearable
            style="width: 320px; max-width: calc(100% - 32px)"
            :placeholder="$t('datasource.search')"
          >
            <template #prefix>
              <el-icon>
                <icon_searchOutline_outlined />
              </el-icon>
            </template>
          </el-input>
        </div>
        <el-icon class="ed-dialog__headerbtn mrt" style="cursor: pointer" @click="close">
          <icon_close_outlined></icon_close_outlined>
        </el-icon>
      </template>
      <div v-if="datasetListWithSearch.length" class="card-content">
        <el-row :gutter="16" class="w-full">
          <el-col
            v-for="ele in datasetListWithSearch"
            :key="ele.id"
            :xs="24"
            :sm="12"
            :md="12"
            :lg="8"
            :xl="6"
            class="mb-16"
          >
            <Card
              :id="ele.id"
              :key="ele.id"
              :name="ele.name"
              :type="String(ele.domain_id || '')"
              :type-name="ele.biz_name"
              :num="(ele.data_set_detail?.dataSetModelConfigs || []).length"
              :is-selected="ele.id === innerDataset"
              :description="ele.description"
              @select-ds="selectDatasetInDialog(ele)"
            ></Card>
          </el-col>
        </el-row>
      </div>
      <template v-if="!keywords && !datasetListWithSearch.length && !searchLoading">
        <EmptyBackground
          class="datasource-yet_btn"
          description="暂无数据集"
          img-type="noneWhite"
        />
      </template>
      <EmptyBackground
        v-if="!!keywords && !datasetListWithSearch.length"
        :description="$t('datasource.relevant_content_found')"
        class="datasource-yet"
        img-type="tree"
      />
      <template #footer>
        <div class="dialog-footer">
          <el-button secondary :disabled="loading" @click="hideDs">{{
            $t('common.cancel')
          }}</el-button>
          <el-button
            :type="loading || innerDataset === undefined ? 'info' : 'primary'"
            :disabled="loading || innerDataset === undefined"
            @click="confirmSelectDataset"
          >
            {{ $t('datasource.confirm') }}
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style lang="less">
.datasource-drawer-chat {
  .ed-drawer__body {
    padding: 16px 0 16px 0;
  }
  .card-content {
    max-height: calc(100% - 40px);
    overflow-y: auto;
    padding: 0 8px 0 24px;

    .w-full {
      width: 100%;
    }

    .mb-16 {
      margin-bottom: 16px;
    }
  }

  .datasource-yet {
    padding-bottom: 0;
    height: auto;
    padding-top: 200px;
  }

  .datasource-yet_btn {
    height: auto !important;
    padding-top: 200px;
    padding-bottom: 0;
  }
}
</style>

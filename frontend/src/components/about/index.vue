<script lang="ts" setup>
import aboutBg from '@/assets/embedded/LOGO-about.png'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
const dialogVisible = ref(false)
const { t } = useI18n()

const open = () => {
  dialogVisible.value = true
}

defineExpose({
  open,
})
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    :title="t('about.title')"
    width="840px"
    modal-class="about-dialog"
  >
    <div class="color-overlay flex-center">
      <img width="368" height="84" :src="aboutBg" />
    </div>
    <div class="content">
      <div class="item">
        <div class="label">{{ $t('about.version') }}</div>
        <div class="value">Numora</div>
      </div>
    </div>
    <div class="name">2014-2026 版权所有 © 杭州飞致云信息科技有限公司</div>
  </el-dialog>
</template>

<style lang="less">
.about-dialog {
  .color-overlay {
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    background: var(--ed-color-primary-1a, #3370ff1a);
    border: 1px solid #dee0e3;
    border-bottom: 0;
    height: 180px;
  }

  .name {
    font-weight: 400;
    font-size: 12px;
    line-height: 22px;
    text-align: center;
    margin-top: 16px;
    color: #8f959e;
  }

  .content {
    border-radius: 6px;
    border: 1px solid #dee0e3;
    border-top-left-radius: 0;
    border-top-right-radius: 0;
    border-top: 0;
    padding: 24px 40px;

    .item {
      font-size: 16px;
      font-style: normal;
      font-weight: 400;
      line-height: 24px;
      margin-bottom: 16px;
      display: flex;
      font-weight: 400;
      .expired-mark {
        color: red;
      }
      .label {
        color: #646a73;
        width: 240px;
      }

      .value {
        margin-left: 24px;
        max-width: 448px;
      }
    }
  }
}
.lic_rooter {
  flex-direction: row;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  align-content: center;
  width: fit-content;
  justify-content: space-between;
  column-gap: 12px;
}
</style>

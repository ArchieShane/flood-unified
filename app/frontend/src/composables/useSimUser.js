import { ref } from 'vue'

/* 仿真页的「当前操作人」。系统还没有登录，先在左侧栏底部的头像上切换（ZN / AL …），
   用于「Mine」筛选与新建任务的 created_by。接登录后改成从登录态取，调用方不用动。 */
const KEY = 'fm.simUser'

function initial() {
  try {
    return localStorage.getItem(KEY) || 'ZN'
  } catch {
    return 'ZN'
  }
}

const user = ref(initial())

export function setSimUser(initials) {
  user.value = initials
  try {
    localStorage.setItem(KEY, initials)
  } catch {
    // 存不下就只在本次会话生效
  }
}

export function useSimUser() {
  return { user, setSimUser }
}

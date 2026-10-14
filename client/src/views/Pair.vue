<template>
    <main class="pair-page">
        <section class="pair-content" aria-labelledby="pair-title">
            <img class="pair-logo" src="/assets/images/logo.svg" alt="HonomiTV">
            <h1 id="pair-title">HonoRebi を連携</h1>
            <div class="pair-profile" aria-live="polite" :aria-busy="checkingAccount">
                <template v-if="checkingAccount">
                    <img class="pair-profile__icon" src="/assets/images/account-icon-default.png" alt="">
                    <span>アカウントを読み込んでいます…</span>
                </template>
                <template v-else-if="userStore.user !== null">
                    <img class="pair-profile__icon" :src="userStore.user_icon_url || '/assets/images/account-icon-default.png'" alt="">
                    <div class="pair-profile__info">
                        <strong>{{userStore.user.name}}</strong>
                        <span>User ID: {{userStore.user.id}}</span>
                    </div>
                    <v-btn class="pair-signout" variant="text" :disabled="loading" @click="signOut">ログアウト</v-btn>
                </template>
                <template v-else-if="accountError">
                    <div class="pair-profile__info">
                        <span>{{accountError}}</span>
                        <v-btn variant="text" @click="loadAccount">再試行</v-btn>
                    </div>
                </template>
                <template v-else>
                    <img class="pair-profile__icon" src="/assets/images/account-icon-default.png" alt="">
                    <div class="pair-profile__info">
                        <strong>ログインしていません</strong>
                        <span>HonomiTV アカウントでログインしてください。</span>
                    </div>
                </template>
            </div>
            <form class="pair-form" @submit.prevent="approve">
                <p v-if="success">連携しました。テレビに戻ってください。</p>
                <p v-else>このテレビを連携するコードを確認してください。</p>
                <v-text-field v-model="formattedCode" class="pair-code-input" label="ペアリングコード"
                    placeholder="ABCD EFGH" autocomplete="one-time-code" autocapitalize="characters"
                    color="primary" variant="outlined" :disabled="checkingAccount || loading || success"
                    :spellcheck="false" />
                <div class="pair-feedback" aria-live="polite">
                    <v-alert v-if="message" type="error">{{message}}</v-alert>
                </div>
                <v-btn v-if="!checkingAccount && !userStore.is_logged_in && !accountError" class="pair-submit"
                    color="secondary" variant="flat" height="52" :to="loginPath">ログイン</v-btn>
                <v-btn v-else class="pair-submit" color="secondary" variant="flat" height="52" type="submit"
                    :loading="loading" :disabled="checkingAccount || !userStore.is_logged_in || code.length !== 8 || success">
                    {{success ? '連携しました' : 'このテレビを許可'}}
                </v-btn>
            </form>
        </section>
    </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import DeviceAuth from '@/services/DeviceAuth';
import useUserStore from '@/stores/UserStore';
import Utils from '@/utils';

const route = useRoute();
const userStore = useUserStore();
const code = ref(String(route.query.code ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8));
const formattedCode = computed({
    get: () => code.value.length > 4 ? `${code.value.slice(0, 4)} ${code.value.slice(4)}` : code.value,
    set: (value: string) => {
        // TV 側と同じ 4 文字区切りで表示しつつ、内部では区切りを含まない 8 文字だけを保持する。
        // これにより、小文字入力や空白・ハイフンを含む貼り付けでも末尾の文字が欠けない。
        code.value = value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
        message.value = '';
    },
});
const loginPath = computed(() => `/login/?return=${encodeURIComponent(route.fullPath)}`);
const checkingAccount = ref(true);
const accountError = ref('');
const loading = ref(false);
const message = ref('');
const success = ref(false);

onMounted(loadAccount);

async function loadAccount() {
    checkingAccount.value = true;
    accountError.value = '';
    try {
        await userStore.fetchUser();
        // トークンを保持したまま取得に失敗した場合、未ログインと誤表示せず再試行を案内する。
        if (Utils.getAccessToken() !== null && userStore.user === null) {
            accountError.value = 'アカウントを読み込めませんでした。再試行してください。';
        }
    } catch {
        accountError.value = 'アカウントを読み込めませんでした。再試行してください。';
    } finally {
        checkingAccount.value = false;
    }
}

function signOut() {
    if (loading.value) return;
    // 設定画面と同じストア処理を使い、コードとログイン後の戻り先は保持する。
    userStore.logout();
    success.value = false;
    message.value = '';
}

async function approve() {
    if (checkingAccount.value || !userStore.is_logged_in || code.value.length !== 8 || loading.value || success.value) return;
    loading.value = true;
    message.value = '';
    try {
        success.value = await DeviceAuth.approve(code.value);
        if (!success.value) {
            message.value = 'コードが無効か、有効期限が切れています。テレビで連携画面を開き直してください。';
        }
    } catch {
        message.value = 'テレビを連携できませんでした。接続を確認して再試行してください。';
    } finally {
        loading.value = false;
    }
}
</script>

<style scoped>
.pair-page {
    min-height: 100vh;
    min-height: 100dvh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 48px 24px;
}
.pair-content { width: 100%; max-width: 480px; }
.pair-logo { display: block; width: 200px; max-width: 60%; height: auto; margin: 0 auto 32px; }
h1 { font-size: 1.65rem; font-weight: 700; text-align: center; margin-bottom: 28px; }
.pair-profile {
    display: flex;
    align-items: center;
    gap: 16px;
    min-height: 96px;
    padding: 16px 0;
    border-top: 1px solid rgb(var(--v-theme-on-background), 0.2);
    border-bottom: 1px solid rgb(var(--v-theme-on-background), 0.2);
}
.pair-profile__icon { width: 56px; height: 56px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
.pair-profile__info { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; overflow-wrap: anywhere; }
.pair-profile__info strong { font-size: 1.1rem; }
.pair-profile__info span { color: rgb(var(--v-theme-on-background), 0.72); font-size: 0.875rem; }
.pair-signout { flex-shrink: 0; }
.pair-form { margin-top: 28px; }
.pair-form > p { min-height: 48px; margin-bottom: 16px; line-height: 1.7; }
.pair-code-input :deep(input) {
    font-family: monospace;
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-align: center;
    text-transform: uppercase;
}
.pair-feedback { min-height: 96px; padding-bottom: 16px; }
.pair-submit { width: 100%; }
@media (max-width: 400px) {
    .pair-page { padding: 32px 20px; }
    .pair-profile { flex-wrap: wrap; align-content: center; min-height: 144px; }
    .pair-profile__info { flex-basis: calc(100% - 72px); }
    .pair-signout { margin-left: auto; }
}
</style>

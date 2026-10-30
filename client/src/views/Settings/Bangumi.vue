<template>
    <!-- ベース画面の中にそれぞれの設定画面で異なる部分を記述する -->
    <SettingsBase>
        <h2 class="settings__heading">
            <a v-ripple class="settings__back-button" @click="$router.back()">
                <Icon icon="fluent:chevron-left-12-filled" width="27px" />
            </a>
            <img src="/assets/images/bangumi.svg" alt="" width="23" height="23">
            <span class="ml-2">Bangumi 連携</span>
        </h2>
        <div class="settings__description">
            <a class="link" href="https://bgm.tv/" target="_blank">Bangumi (bgm.tv)</a> の個人アクセストークンを使ってアカウントを連携します。<br>
            今後、視聴した番組を Bangumi の視聴状況へ反映する機能で利用されます。<br>
        </div>
        <div class="settings__content" :class="{'settings__content--loading': isLoading}">
            <div class="bangumi-account bangumi-account--anonymous"
                v-if="isLoading === false && (userStore.user === null || userStore.user.bangumi_user_id === null)">
                <div class="bangumi-account-wrapper">
                    <img class="bangumi-brand-icon flex-shrink-0" src="/assets/images/bangumi.svg" alt="" width="45" height="45">
                    <div class="bangumi-account__info ml-4">
                        <div class="bangumi-account__info-name">Bangumi アカウントと連携していません</div>
                        <span class="bangumi-account__info-description">
                            KonomiTV アカウントへのログインと、Bangumi の個人アクセストークンが必要です。
                        </span>
                    </div>
                </div>
                <v-btn class="bangumi-account__button ml-auto" color="secondary" width="130" height="56" variant="flat"
                    @click="openAccountLinkDialog()">
                    <Icon icon="fluent:plug-connected-20-filled" class="mr-2" height="26" />連携する
                </v-btn>
            </div>
            <div class="bangumi-account"
                v-if="isLoading === false && userStore.user !== null && userStore.user.bangumi_user_id !== null">
                <div class="bangumi-account-wrapper">
                    <img class="bangumi-account__icon" :src="userStore.user.bangumi_user_avatar_url ?? ''">
                    <div class="bangumi-account__info">
                        <div class="bangumi-account__info-name">{{userStore.user.bangumi_user_nickname}} と連携しています</div>
                        <span class="bangumi-account__info-description">
                            <a class="link" :href="`https://bgm.tv/user/${userStore.user.bangumi_user_name}`" target="_blank">
                                @{{userStore.user.bangumi_user_name}}
                            </a>
                            <span class="ml-2">User ID: {{userStore.user.bangumi_user_id}}</span>
                        </span>
                    </div>
                </div>
                <div class="bangumi-account__actions ml-auto">
                    <v-btn color="secondary" variant="outlined" height="42" @click="openAccountLinkDialog()">再連携</v-btn>
                    <v-btn color="secondary" variant="flat" height="42" @click="logoutBangumiAccount()">
                        <Icon icon="fluent:plug-disconnected-20-filled" class="mr-2" height="22" />連携解除
                    </v-btn>
                </div>
            </div>
            <div class="settings__item">
                <div class="settings__item-heading">個人アクセストークンについて</div>
                <div class="settings__item-label">
                    連携時に専用の 30 日トークンを作成し、有効期限が切れる前に自動更新します。<br>
                    長期間のサーバー停止やトークン失効で更新できなかった場合は、再連携してください。<br>
                    入力されたトークンは KonomiTV サーバーのデータベースへ暗号化して保存され、クライアントには返されません。<br>
                </div>
                <v-btn class="settings__save-button mt-4" variant="flat" href="https://next.bgm.tv/demo/access-token" target="_blank">
                    <Icon icon="fluent:key-20-filled" height="21px" />
                    <span class="ml-2">Bangumi で個人アクセストークンを作成</span>
                </v-btn>
            </div>
        </div>

        <!-- 個人アクセストークンは認証情報なので、通常の設定値とは分けて連携操作時だけ入力する -->
        <v-dialog width="550" v-model="accountLinkDialog">
            <v-card class="px-2 py-2">
                <v-card-title class="d-flex justify-center pt-6 font-weight-bold">Bangumi アカウントと連携</v-card-title>
                <v-card-text class="px-6 pt-4 pb-2">
                    <div class="mb-4">
                        Bangumi で作成した個人アクセストークンを入力してください。<br>
                        トークンは作成直後に一度だけ表示されます。
                    </div>
                    <v-text-field color="primary" variant="outlined" label="個人アクセストークン" autocomplete="off"
                        :type="accessTokenShowing ? 'text' : 'password'"
                        :append-inner-icon="accessTokenShowing ? 'fa-solid:eye-slash' : 'fa-solid:eye'"
                        v-model="accessToken" @click:appendInner="accessTokenShowing = !accessTokenShowing">
                    </v-text-field>
                </v-card-text>
                <v-card-actions class="px-6 pb-5">
                    <v-spacer></v-spacer>
                    <v-btn variant="text" @click="closeAccountLinkDialog()">キャンセル</v-btn>
                    <v-btn color="secondary" variant="flat" :loading="linking" :disabled="accessToken.trim() === ''"
                        @click="loginBangumiAccount()">連携する</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
    </SettingsBase>
</template>
<script lang="ts" setup>

import { onMounted, ref } from 'vue';

import Message from '@/message';
import Bangumi from '@/services/Bangumi';
import useUserStore from '@/stores/UserStore';
import SettingsBase from '@/views/Settings/Base.vue';


const userStore = useUserStore();

// 個人アクセストークンはダイアログを閉じるたびに破棄し、画面上へ残さない
const accountLinkDialog = ref(false);
const accessToken = ref('');
const accessTokenShowing = ref(false);
const linking = ref(false);
const isLoading = ref(true);


// URL から Bangumi 設定へ直接入った場合も、保存済み KonomiTV ログインセッションから連携状態を復元する
onMounted(async () => {
    await userStore.fetchUser();
    isLoading.value = false;
});


/**
 * Bangumi アカウント連携ダイアログを開く。
 */
function openAccountLinkDialog(): void {

    // KonomiTV ユーザーへ紐づける必要があるため、未ログイン状態では認証情報を入力させない
    if (userStore.user === null) {
        Message.warning('連携をはじめるには、KonomiTV アカウントにログインしてください。');
        return;
    }

    accountLinkDialog.value = true;
}


/**
 * Bangumi アカウント連携ダイアログを閉じる。
 */
function closeAccountLinkDialog(): void {

    accountLinkDialog.value = false;
    accessToken.value = '';
    accessTokenShowing.value = false;
}


/**
 * 入力された個人アクセストークンで Bangumi アカウントを連携する。
 */
async function loginBangumiAccount(): Promise<void> {

    // 連打による同一認証リクエストの重複を防ぐ
    if (linking.value === true || accessToken.value.trim() === '') return;
    linking.value = true;

    try {
        const result = await Bangumi.loginAccount({access_token: accessToken.value});
        if (result === false) return;

        // 連携した公開プロフィールを画面へ反映し、入力済みトークンを破棄する
        await userStore.fetchUser(true);
        closeAccountLinkDialog();
        Message.success('Bangumi アカウントと連携しました。');
    } finally {
        linking.value = false;
    }
}


/**
 * 現在の KonomiTV ユーザーから Bangumi アカウント連携を解除する。
 */
async function logoutBangumiAccount(): Promise<void> {

    const result = await Bangumi.logoutAccount();
    if (result === false) return;

    // 連携解除後の未連携状態を画面へ反映する
    await userStore.fetchUser(true);
    Message.success('Bangumi アカウントとの連携を解除しました。');
}

</script>
<style lang="scss" scoped>

.bangumi-account {
    display: flex;
    align-items: center;
    min-height: 120px;
    padding: 20px;
    border-radius: 15px;
    background: rgb(var(--v-theme-background-lighten-2));
    @include tablet-horizontal {
        align-items: stretch;
        flex-direction: column;
    }
    @include smartphone-horizontal {
        align-items: stretch;
        flex-direction: column;
        padding: 16px;
        border-radius: 10px;
    }
    @include smartphone-vertical {
        align-items: stretch;
        flex-direction: column;
        padding: 16px 12px;
        border-radius: 10px;
    }

    &-wrapper {
        display: flex;
        align-items: center;
        min-width: 0;
    }

    &__icon {
        flex-shrink: 0;
        width: 80px;
        height: 80px;
        border-radius: 50%;
        object-fit: cover;
        background: linear-gradient(150deg, rgb(var(--v-theme-gray)), rgb(var(--v-theme-background-lighten-2)));
        @include smartphone-vertical {
            width: 60px;
            height: 60px;
        }
    }

    &__info {
        display: flex;
        flex-direction: column;
        min-width: 0;
        margin-left: 20px;
        margin-right: 16px;
        @include smartphone-vertical {
            margin-left: 12px;
            margin-right: 0;
        }

        &-name {
            overflow: hidden;
            font-size: 19px;
            font-weight: bold;
            text-overflow: ellipsis;
            white-space: nowrap;
            @include smartphone-vertical {
                font-size: 16px;
            }
        }

        &-description {
            margin-top: 6px;
            font-size: 13px;
            line-height: 1.5;
            color: rgb(var(--v-theme-text-darken-1));
        }
    }

    &__button,
    &__actions {
        flex-shrink: 0;
        @include tablet-horizontal {
            margin-top: 16px;
        }
        @include smartphone-horizontal {
            margin-top: 16px;
        }
        @include smartphone-vertical {
            margin-top: 16px;
        }
    }

    &__actions {
        display: flex;
        gap: 8px;
    }

    &--anonymous {
        @include smartphone-horizontal-short {
            .bangumi-account-wrapper > .bangumi-brand-icon {
                display: none;
            }
            .bangumi-account__info {
                margin-left: 0 !important;
            }
        }
        @include smartphone-vertical {
            .bangumi-account-wrapper > .bangumi-brand-icon {
                display: none;
            }
            .bangumi-account__info {
                margin-left: 0 !important;
            }
        }
    }
}

</style>

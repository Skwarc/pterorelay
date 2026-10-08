import { PageContentBlock, useExtensionTranslation } from '@pterodactyl/sdk';

export default function AccountSettings() {
    const { t } = useExtensionTranslation('messages');
    return <PageContentBlock title="PteroSync">
        <div className="psync:rounded-lg psync:border psync:border-border psync:bg-card psync:p-6 psync:text-foreground">
            {t('account_help')}
        </div>
    </PageContentBlock>;
}

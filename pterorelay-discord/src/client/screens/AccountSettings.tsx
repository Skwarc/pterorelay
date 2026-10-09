import { PageContentBlock, useExtensionTranslation } from '@pterodactyl/sdk';

export default function AccountSettings() {
    const { t } = useExtensionTranslation('messages');
    return <PageContentBlock title="PteroRelay">
        <div className="prelay:rounded-lg prelay:border prelay:border-border prelay:bg-card prelay:p-6 prelay:text-foreground">
            {t('account_help')}
        </div>
    </PageContentBlock>;
}

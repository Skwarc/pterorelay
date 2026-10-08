import './styles.css';
import { definePterodactylExtension } from '@pterodactyl/sdk';

export default definePterodactylExtension({
    setup({ screens }) {
        screens.register('account-settings', () => import('./screens/AccountSettings'));
        screens.register('server-settings', () => import('./screens/ServerSettings'));
        screens.register('admin-settings', () => import('./screens/AdminSettings'));
    },
});

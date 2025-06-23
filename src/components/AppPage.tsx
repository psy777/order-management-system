'use client';

import { Dashboard } from './views';
import { useAppContext } from '../context/AppContext';

export default function AppPage({ orders, allVendors }: { orders: any[], allVendors: any[] }) {
    const { allSelectableItems, setOrderForEmailModal } = useAppContext();
    return (
        <Dashboard
            orders={orders}
            allVendors={allVendors}
            allSelectableItems={allSelectableItems}
            setOrderForEmailModal={setOrderForEmailModal}
        />
    );
}

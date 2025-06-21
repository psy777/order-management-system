import React from 'react';
import { useRouter } from 'next/router';
import { OrderForm } from '../../components/views';
import { OrderFormData } from '../../components/views';

interface ViewOrderPageProps {
    orders: OrderFormData[];
    saveOrder: (order: OrderFormData) => Promise<void>;
    deleteOrder: (orderId: string, deletePayload: any) => Promise<void>;
    allVendors: any[];
    allSelectableItems: any;
    itemData: any;
    packageData: any;
    fetchAndUpdateVendors: () => Promise<void>;
    setOrderForEmailModal: (order: OrderFormData | null) => void;
}

const ViewOrderPage: React.FC<ViewOrderPageProps> = (props) => {
    const router = useRouter();
    const { id } = router.query;
    const order = props.orders.find(o => o.id === id);

    if (!order) {
        return <div>Order not found</div>;
    }

    return <OrderForm {...props} order={order} />;
};

export default ViewOrderPage;

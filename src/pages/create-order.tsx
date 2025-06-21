import React from 'react';
import { OrderForm } from '../components/views';
import { OrderFormData } from '../components/views';

interface CreateOrderPageProps {
    saveOrder: (order: OrderFormData) => Promise<void>;
    deleteOrder: (orderId: string, deletePayload: any) => Promise<void>;
    allVendors: any[];
    allSelectableItems: any;
    itemData: any;
    packageData: any;
    fetchAndUpdateVendors: () => Promise<void>;
    setOrderForEmailModal: (order: OrderFormData | null) => void;
}

const CreateOrderPage: React.FC<CreateOrderPageProps> = (props) => {
    return <OrderForm {...props} />;
};

export default CreateOrderPage;

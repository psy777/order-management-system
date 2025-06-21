import React from 'react';
import { Dashboard } from '../components/views';
import { OrderFormData } from '../components/views';
import Link from 'next/link';

interface HomePageProps {
    orders: OrderFormData[];
    allVendors: any[];
    allSelectableItems: any;
    setOrderForEmailModal: (order: OrderFormData | null) => void;
    isLoading: boolean;
}

const HomePage: React.FC<HomePageProps> = ({ orders, allVendors, allSelectableItems, setOrderForEmailModal, isLoading }) => {
    if (isLoading) {
        return <div className="text-center p-8">Loading...</div>;
    }

    const viewOrder = (order: OrderFormData) => {
        // This will be handled by a separate page now
    };

    return (
        <Dashboard 
            orders={orders} 
            viewOrder={viewOrder} 
            allVendors={allVendors} 
            allSelectableItems={allSelectableItems} 
            setOrderForEmailModal={setOrderForEmailModal} 
        />
    );
};

export default HomePage;

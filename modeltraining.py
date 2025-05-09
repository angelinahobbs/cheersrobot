# Training Pipeline

# imports
!pip install -q torch torchvision pandas scikit-learn opencv-python

import os
import json
import pandas as pd
import numpy as np
from glob import glob
from PIL import Image
import torch
import torch.nn as nn
import torchvision.transforms as T
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import MinMaxScaler
import matplotlib.pyplot as plt

# load and structure data

log_dir = '/content/manual_logs'
json_files = sorted(glob(os.path.join(log_dir, '*.json')))

samples = []

for jf in json_files:
    with open(jf) as f:
        data = json.load(f)

    detections = data.get('detections', [])
    target = held = None

    if len(detections) == 1:
        target = detections[0]
    elif len(detections) >= 2:
        # heuristic: target is further from camera (lower in image)
        detections = sorted(detections, key=lambda d: d['cy'])
        held = detections[-1]   # closer to gripper
        target = detections[0]  # farther on table

    row = {
        'image': data['frame'],
        'target_cx': target['cx'] if target else None,
        'target_cy': target['cy'] if target else None,
        'held_cx': held['cx'] if held else None,
        'held_cy': held['cy'] if held else None,
    }

    for i in range(1, 7):
        row[f'servo_{i}'] = data['servo_positions'].get(str(i))

    samples.append(row)

# clean and save to DataFrame
df = pd.DataFrame(samples)
df.dropna(inplace=True)
df.reset_index(drop=True, inplace=True)
print(f"Loaded {len(df)} valid samples.")
df.head()

# normalize servo values
servo_cols = [f'servo_{i}' for i in range(1, 7)]
scaler = MinMaxScaler()
df[servo_cols] = scaler.fit_transform(df[servo_cols])

# pytorch dataset

class CheersDataset(Dataset):
    def __init__(self, df, transform=None):
        self.df = df
        self.transform = transform or T.Compose([
            T.Resize((224, 224)),
            T.ToTensor(),
        ])

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        img = Image.open(row['image']).convert('RGB')
        img = self.transform(img)

        coords = torch.tensor([
            row['target_cx'], row['target_cy'],
            row['held_cx'], row['held_cy']
        ], dtype=torch.float32)
        coords /= 640.0

        target = torch.tensor(row[servo_cols].values, dtype=torch.float32)
        return img, coords, target

# import model and initialize

import torchvision.models as models

class ServoRegressor(nn.Module):
    def __init__(self):
        super().__init__()
        base = models.resnet18(weights='IMAGENET1K_V1')
        self.backbone = nn.Sequential(*list(base.children())[:-1])
        self.regressor = nn.Sequential(
            nn.Linear(512 + 4, 128),
            nn.ReLU(),
            nn.Linear(128, 6)
        )

    def forward(self, img, coords):
        x = self.backbone(img)
        x = x.view(x.size(0), -1)
        x = torch.cat([x, coords], dim=1)
        return self.regressor(x)

# training

dataset = CheersDataset(df)
dataloader = DataLoader(dataset, batch_size=16, shuffle=True)

model = ServoRegressor()
optimizer = torch.optim.Adam(model.parameters(), lr=1e-4)
criterion = nn.MSELoss()

epochs = 30
model.train()
for epoch in range(epochs):
    total_loss = 0
    for img, coords, target in dataloader:
        pred = model(img, coords)
        loss = criterion(pred, target)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
    print(f"Epoch {epoch+1}/{epochs} | Loss: {total_loss/len(dataloader):.4f}")

# save model & scaler
torch.save(model.state_dict(), 'cheers_model.pt')
import joblib
joblib.dump(scaler, 'servo_scaler.save')
print("✅ Model and scaler saved.")

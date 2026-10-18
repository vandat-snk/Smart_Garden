import torch
from torch import nn


class GardenCNN(nn.Module):
    def __init__(self, channels=16, dropout=0.2):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv1d(4, channels, 3, padding=1), nn.GELU(),
            nn.Conv1d(channels, channels, 3, padding=1), nn.GELU(), nn.Dropout(dropout),
            nn.Conv1d(channels, channels, 3, dilation=2, padding=2), nn.GELU())
        self.head = nn.Sequential(nn.Linear(channels * 2, channels), nn.GELU(), nn.Dropout(dropout))
        self.classifier = nn.Linear(channels, 1)
        self.regressor = nn.Linear(channels, 1)

    def forward(self, x):
        z = self.features(x)
        z = self.head(torch.cat([z.mean(dim=2), z[:, :, -1]], dim=1))
        return self.classifier(z).squeeze(1), torch.sigmoid(self.regressor(z)).squeeze(1) * 30
